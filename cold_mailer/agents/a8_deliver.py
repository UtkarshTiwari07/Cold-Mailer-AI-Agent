"""Agent 8 — Email Delivery.

Every guard here exists because "send the email" is the one action in this
whole pipeline that can't be undone. In order: suppression list, duplicate
check (the `messages` table's `UNIQUE(lead_id, touch)` constraint is the
hard backstop — this check is the friendly version that avoids even
attempting a doomed insert), then the send-budget gate (warm-up ramp,
business hours, bounce circuit breaker), and only then an actual send.
Human approval itself isn't checked here — it's enforced upstream, by the
approval-queue UI being the only thing that enqueues a `send_message` task
in the first place (see `web/app.py`), so a draft can't reach this function
without a human having already said yes.
"""

from __future__ import annotations

from cold_mailer.contracts.a8_deliver import DeliveryOutput, DeliveryStatus
from cold_mailer.core.config import get_settings
from cold_mailer.core.db import acquire
from cold_mailer.core.logging import get_logger
from cold_mailer.pipeline.send_budget import check_and_reserve_slot, record_sent
from cold_mailer.pipeline.stages import task_handler
from cold_mailer.pipeline.state_machine import Task, record_stage_run
from cold_mailer.providers.transport.base import Transport
from cold_mailer.providers.transport.console import ConsoleTransport

log = get_logger(component="a8_deliver", agent="A8")

_transport_instance: Transport | None = None


def get_transport() -> Transport:
    global _transport_instance
    if _transport_instance is not None:
        return _transport_instance

    kind = get_settings().delivery.transport
    if kind == "gmail":
        from cold_mailer.providers.transport.gmail import GmailTransport

        _transport_instance = GmailTransport()
    elif kind == "smtp":
        from cold_mailer.providers.transport.smtp import SMTPTransport

        # Host/port/credentials for generic SMTP are deployment-specific and
        # not modeled as first-class settings in this MVP (Gmail API is the
        # transport this project actually uses) — wire real values here if
        # SMTP becomes the chosen path.
        _transport_instance = SMTPTransport(host="smtp.example.com")
    else:
        _transport_instance = ConsoleTransport()
    return _transport_instance


async def _is_suppressed(to_email: str, domain: str) -> bool:
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM suppressions WHERE (scope = 'email' AND value = $1) "
            "OR (scope = 'domain' AND value = $2) LIMIT 1",
            to_email, domain,
        )
    return row is not None


async def deliver(
    lead_id: int, touch: int, to_email: str, subject: str, body: str, thread_id: str | None = None
) -> DeliveryOutput:
    domain = to_email.split("@")[-1]

    if await _is_suppressed(to_email, domain):
        log.info("a8.skipped_suppressed", lead_id=lead_id, to=to_email)
        return DeliveryOutput(lead_id=lead_id, touch=touch, status=DeliveryStatus.skipped_suppressed)

    async with acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM messages WHERE lead_id = $1 AND touch = $2", lead_id, touch
        )
    if existing:
        log.info("a8.skipped_duplicate", lead_id=lead_id, touch=touch)
        return DeliveryOutput(lead_id=lead_id, touch=touch, status=DeliveryStatus.skipped_duplicate)

    allowed, reason = await check_and_reserve_slot()
    if not allowed:
        log.warning("a8.skipped_budget", lead_id=lead_id, reason=reason)
        return DeliveryOutput(lead_id=lead_id, touch=touch, status=DeliveryStatus.skipped_budget, detail=reason)

    transport = get_transport()
    try:
        result = await transport.send(to_email, subject, body, thread_id)
    except Exception as exc:  # noqa: BLE001 - report as a normal failed result, let the task queue retry
        log.error("a8.send_failed", lead_id=lead_id, to=to_email, error=str(exc))
        await record_stage_run(
            subject_type="lead", subject_id=lead_id, agent="A8", status="error", error=str(exc),
        )
        return DeliveryOutput(lead_id=lead_id, touch=touch, status=DeliveryStatus.failed, detail=str(exc))

    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO messages
                (lead_id, touch, transport, provider_message_id, provider_thread_id,
                 from_email, to_email, subject, body, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'sent')
            """,
            lead_id, touch, transport.name, result.provider_message_id, result.provider_thread_id,
            get_settings().delivery.from_email, to_email, subject, body,
        )
        await conn.execute(
            "INSERT INTO events (lead_id, type, payload) VALUES ($1, 'sent', $2::jsonb)",
            lead_id, f'{{"touch": {touch}, "transport": "{transport.name}"}}',
        )
        await conn.execute("UPDATE leads SET status = 'sent', updated_at = now() WHERE id = $1", lead_id)

    await record_sent()
    await record_stage_run(subject_type="lead", subject_id=lead_id, agent="A8", status="ok", confidence=1.0)

    log.info("a8.sent", lead_id=lead_id, touch=touch, transport=transport.name, to=to_email)
    return DeliveryOutput(
        lead_id=lead_id, touch=touch, status=DeliveryStatus.sent,
        provider_message_id=result.provider_message_id, provider_thread_id=result.provider_thread_id,
    )


@task_handler("send_message")
async def _handle_send_message(task: Task) -> None:
    payload = task.payload
    required = {"to_email", "subject", "body", "touch"}
    if not required.issubset(payload):
        raise ValueError(f"send_message task {task.id} missing fields: {required - set(payload)}")
    await deliver(
        task.subject_id, payload["touch"], payload["to_email"], payload["subject"], payload["body"],
        payload.get("thread_id"),
    )
