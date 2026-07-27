"""Deliverability safety rails, all enforced here rather than left as
documentation. Personal-Gmail sending was a knowing, risk-accepted decision
(see DESIGN.md) — this module is what carries that risk responsibly: a
warm-up ramp, business-hours/weekday gating, and a bounce-rate circuit
breaker that halts sending outright rather than trusting a human to notice
a rising bounce rate before Gmail's own throttling kicks in.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cold_mailer.core.config import get_settings
from cold_mailer.core.db import acquire
from cold_mailer.core.logging import get_logger

log = get_logger(component="send_budget")


async def _warmup_days_elapsed() -> int:
    """Counts calendar days that have ever recorded a send — this is what
    "day N of warm-up" means, not simply days-since-launch, so a pause in
    sending doesn't fast-forward the ramp."""
    async with acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM send_budget WHERE sent > 0")
    return count or 0


async def _today_cap() -> int:
    settings = get_settings().delivery
    day_index = await _warmup_days_elapsed()
    if day_index < len(settings.warmup_daily_caps):
        return settings.warmup_daily_caps[day_index]
    return settings.steady_state_cap


async def _get_or_create_today_row() -> dict:
    today = datetime.now(UTC).date()
    cap = await _today_cap()
    async with acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO send_budget (day, sent, cap) VALUES ($1, 0, $2) "
            "ON CONFLICT (day) DO UPDATE SET day = EXCLUDED.day "
            "RETURNING day, sent, cap, halted, halt_note",
            today, cap,
        )
    return dict(row)


async def _recent_bounce_rate() -> float:
    settings = get_settings().delivery
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT status FROM messages ORDER BY sent_at DESC LIMIT $1", settings.bounce_rate_window
        )
    if len(rows) < 10:
        return 0.0  # not enough history yet to judge — avoid halting on noise from a handful of sends
    bounced = sum(1 for r in rows if r["status"] == "bounced")
    return bounced / len(rows)


async def _within_business_hours() -> bool:
    settings = get_settings().delivery
    now = datetime.now(UTC)
    if settings.send_weekdays_only and now.weekday() >= 5:
        return False
    start, end = settings.business_hours
    return start <= now.hour < end


async def check_and_reserve_slot() -> tuple[bool, str]:
    """The single gate A8 calls before every send. Returns (allowed, reason).
    On the first call to breach the bounce threshold, this also flips
    `halted=True` on today's row so every subsequent check short-circuits
    without re-querying `messages` — the halt is sticky for the rest of the
    day once tripped, not re-evaluated send-by-send."""
    settings = get_settings().delivery
    row = await _get_or_create_today_row()

    if row["halted"]:
        return False, row["halt_note"] or "sending halted"

    if not settings.allow_unsafe_override and not await _within_business_hours():
        return False, "outside configured business hours/weekdays"

    if row["sent"] >= row["cap"]:
        return False, f"daily cap reached ({row['sent']}/{row['cap']})"

    bounce_rate = await _recent_bounce_rate()
    if bounce_rate > settings.bounce_rate_halt_threshold:
        note = f"bounce rate {bounce_rate:.1%} exceeds {settings.bounce_rate_halt_threshold:.1%} threshold"
        async with acquire() as conn:
            await conn.execute(
                "UPDATE send_budget SET halted = TRUE, halt_note = $1 WHERE day = $2", note, row["day"]
            )
        log.error("send_budget.circuit_breaker_tripped", bounce_rate=bounce_rate, note=note)
        return False, note

    return True, "ok"


async def record_sent() -> None:
    today = datetime.now(UTC).date()
    async with acquire() as conn:
        await conn.execute(
            "UPDATE send_budget SET sent = sent + 1 WHERE day = $1", today
        )
