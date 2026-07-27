"""Agent 10 — Reply Triage (gap-filled, see contracts/a10_triage.py).

Classification is deterministic pattern matching wherever the signal is
reliable enough for it — bounces and out-of-office auto-responders have
near-universal textual markers, and getting these two right is the entire
point of this agent: an OOO auto-reply must never be counted as a genuine
reply (it would corrupt Agent 9's reply-rate stats), and a hard bounce must
both stop the follow-up sequence AND land on the permanent suppression list
(sending again would just harm domain reputation for nothing). An LLM call
is used only for the genuinely ambiguous case — sentiment on a real reply —
where no regex substitutes for judgment.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from cold_mailer.contracts.a10_triage import ReplyKind, TriageInput, TriageOutput
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.pipeline.stages import task_handler
from cold_mailer.pipeline.state_machine import Task, record_stage_run

log = get_logger(component="a10_triage", agent="A10")

_OOO_PATTERNS = [
    r"\bout of (the )?office\b", r"\bauto(matic)?[- ]?reply\b", r"\bvacation responder\b",
    r"\bon leave\b", r"\bcurrently away\b", r"\bi(?:'m| am) (?:currently )?(?:on|out)\b.{0,20}\b(vacation|leave|pto)\b",
    r"\breturn(?:ing)? (?:to the office |to work )?on\b",
]

_HARD_BOUNCE_PATTERNS = [
    r"\bdoes not exist\b", r"\bno such user\b", r"\buser unknown\b", r"\b550[- ]?5\.1\.1\b",
    r"\baddress rejected\b", r"\brecipient (?:address )?rejected\b", r"\bmailbox (?:not found|unavailable)\b",
    r"\bpermanent(?:ly)? (?:failed|failure)\b",
]

_SOFT_BOUNCE_PATTERNS = [
    r"\bmailbox full\b", r"\bquota exceeded\b", r"\btemporar(?:y|ily) (?:failed|unavailable|deferred)\b",
    r"\b4\d\d[- ]?\d\.\d\.\d\b",  # 4xx SMTP codes
]

_BOUNCE_SENDER_MARKERS = [
    r"mailer-daemon", r"postmaster@", r"delivery status notification", r"undeliverable",
]

_UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b", r"\bremove me\b", r"\bstop (?:emailing|contacting) me\b",
    r"\bplease don'?t (?:email|contact) me\b", r"\btake me off\b",
]


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


class _SentimentResult(BaseModel):
    """Deliberately NOT a TriageOutput subclass: TriageOutput requires
    `lead_id`/`kind`/`should_stop_sequence`/`should_suppress`, none of which
    an LLM asked only for sentiment has any business inventing. Forcing the
    full schema would make it fabricate a lead_id or a bounce/OOO kind out
    of nothing just to satisfy validation."""

    sentiment: Literal["positive", "neutral", "negative"]


def _stub_sentiment() -> _SentimentResult:
    return _SentimentResult(sentiment="neutral")


async def classify_reply(triage_input: TriageInput) -> TriageOutput:
    subject = triage_input.subject or ""
    body = triage_input.body or ""
    combined = f"{subject}\n{body}"
    from_header = str(triage_input.headers.get("From", "")).lower()

    is_bounce_sender = _matches_any(_BOUNCE_SENDER_MARKERS, from_header) or _matches_any(
        _BOUNCE_SENDER_MARKERS, combined
    )
    if is_bounce_sender and _matches_any(_HARD_BOUNCE_PATTERNS, combined):
        return TriageOutput(
            lead_id=triage_input.lead_id, kind=ReplyKind.bounce_hard,
            should_stop_sequence=True, should_suppress=True, confidence="high",
        )
    if is_bounce_sender and _matches_any(_SOFT_BOUNCE_PATTERNS, combined):
        return TriageOutput(
            lead_id=triage_input.lead_id, kind=ReplyKind.bounce_soft,
            should_stop_sequence=True, should_suppress=False, confidence="high",
        )
    if is_bounce_sender:
        # A bounce-shaped sender we couldn't further classify — treat as a
        # (conservative) hard bounce rather than risk it being read as a
        # genuine reply and corrupting reply-rate stats.
        return TriageOutput(
            lead_id=triage_input.lead_id, kind=ReplyKind.bounce_hard,
            should_stop_sequence=True, should_suppress=True, confidence="low",
        )

    if _matches_any(_OOO_PATTERNS, combined):
        return TriageOutput(
            lead_id=triage_input.lead_id, kind=ReplyKind.out_of_office,
            should_stop_sequence=False, should_suppress=False, confidence="high",
        )

    if _matches_any(_UNSUBSCRIBE_PATTERNS, combined):
        return TriageOutput(
            lead_id=triage_input.lead_id, kind=ReplyKind.unsubscribe,
            should_stop_sequence=True, should_suppress=True, confidence="high",
        )

    # Genuine reply: the one case worth spending an LLM call on, for sentiment.
    result = await complete_structured(
        tier="flash",
        output_type=_SentimentResult,
        system_prompt=(
            "Classify the sentiment of this reply to a cold outreach email as exactly one of: "
            "positive, neutral, negative."
        ),
        user_prompt=combined,
        stub_factory=_stub_sentiment,
    )
    return TriageOutput(
        lead_id=triage_input.lead_id, kind=ReplyKind.genuine_reply, sentiment=result.value.sentiment,
        should_stop_sequence=True, should_suppress=False, confidence="medium",
    )


async def triage_message(lead_id: int, message_id: int, subject: str, body: str, headers: dict | None = None) -> TriageOutput:
    output = await classify_reply(
        TriageInput(lead_id=lead_id, message_id=message_id, subject=subject, body=body, headers=headers or {})
    )

    import json

    from cold_mailer.core.db import acquire

    async with acquire() as conn:
        event_type = "bounced" if output.kind in (ReplyKind.bounce_hard, ReplyKind.bounce_soft) else (
            "unsubscribed" if output.kind == ReplyKind.unsubscribe else (
                "auto_reply" if output.kind == ReplyKind.out_of_office else "replied"
            )
        )
        await conn.execute(
            "INSERT INTO events (lead_id, message_id, type, payload) VALUES ($1,$2,$3,$4::jsonb)",
            lead_id, message_id, event_type,
            json.dumps({"kind": output.kind.value, "sentiment": output.sentiment}),
        )
        if output.kind in (ReplyKind.bounce_hard, ReplyKind.bounce_soft):
            await conn.execute("UPDATE messages SET status = 'bounced' WHERE id = $1", message_id)
        elif output.kind == ReplyKind.genuine_reply:
            await conn.execute("UPDATE messages SET status = 'replied' WHERE id = $1", message_id)

        if output.should_suppress:
            email = await conn.fetchval("SELECT email FROM leads WHERE id = $1", lead_id)
            if email:
                await conn.execute(
                    "INSERT INTO suppressions (scope, value, reason) VALUES ('email', $1, $2) "
                    "ON CONFLICT DO NOTHING",
                    email, f"a10_triage:{output.kind.value}",
                )

        if output.should_stop_sequence:
            await conn.execute(
                "UPDATE tasks SET status = 'dead', last_error = 'sequence stopped by A10' "
                "WHERE subject_type = 'lead' AND subject_id = $1 AND kind IN ('generate_draft', 'send_message') "
                "AND status = 'pending'",
                lead_id,
            )
            await conn.execute(
                "UPDATE leads SET status = $1, updated_at = now() WHERE id = $2",
                ("replied" if output.kind == ReplyKind.genuine_reply else "suppressed"), lead_id,
            )

    await record_stage_run(
        subject_type="lead", subject_id=lead_id, agent="A10", status="ok", confidence=1.0,
    )
    log.info("a10.triaged", lead_id=lead_id, kind=output.kind.value, stop=output.should_stop_sequence)
    return output


@task_handler("triage_inbox")
async def _handle_triage_inbox(task: Task) -> None:
    """Placeholder for the periodic Gmail-polling entry point: in
    production this lists new messages via the Gmail API's `history.list`
    since a stored `historyId`, matches each to a lead by thread/sender, and
    calls `triage_message` for each. Polling requires live OAuth credentials
    this environment doesn't have, so it isn't implemented as a live network
    call here — `triage_message`/`classify_reply` above are the tested,
    reusable logic; wiring them to a real Gmail poll is the integration
    point when credentials exist.
    """
    payload = task.payload
    if not payload.get("subject") and not payload.get("body"):
        log.info("a10.no_op_poll_tick", note="no test payload given, nothing to triage")
        return
    await triage_message(
        task.subject_id, payload["message_id"], payload.get("subject", ""), payload.get("body", ""),
        payload.get("headers"),
    )
