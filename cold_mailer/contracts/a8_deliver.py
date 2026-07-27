"""Agent 8 — Email Delivery."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DeliveryStatus(str, Enum):
    sent = "sent"
    skipped_suppressed = "skipped_suppressed"
    skipped_budget = "skipped_budget"
    skipped_duplicate = "skipped_duplicate"
    failed = "failed"


class DeliveryInput(BaseModel):
    lead_id: int
    touch: int
    to_email: str
    subject: str
    body: str
    thread_id: str | None = None  # set for touch 2/3 to reply in-thread


class DeliveryOutput(BaseModel):
    lead_id: int
    touch: int
    status: DeliveryStatus
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    detail: str | None = None
    sent_at: str | None = None


class SendBudgetState(BaseModel):
    day: str
    sent: int
    cap: int
    halted: bool
    halt_note: str | None = None
    remaining: int = Field(ge=0)
