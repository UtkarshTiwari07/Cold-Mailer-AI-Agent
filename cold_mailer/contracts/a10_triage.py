"""Agent 10 — Reply Triage (added: the brief says "track responses" but never
specifies how a reply is distinguished from an out-of-office auto-responder
or a bounce — getting this wrong means either spamming someone who already
said no, or miscounting OOO replies as real interest in Agent 9's stats).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ReplyKind(str, Enum):
    genuine_reply = "genuine_reply"
    out_of_office = "out_of_office"
    bounce_hard = "bounce_hard"
    bounce_soft = "bounce_soft"
    unsubscribe = "unsubscribe"
    unrelated = "unrelated"


class TriageInput(BaseModel):
    lead_id: int
    message_id: int
    subject: str
    body: str
    headers: dict = {}


class TriageOutput(BaseModel):
    lead_id: int
    kind: ReplyKind
    sentiment: str | None = None  # 'positive' | 'neutral' | 'negative', only for genuine_reply
    should_stop_sequence: bool
    should_suppress: bool
    confidence: str = "medium"
