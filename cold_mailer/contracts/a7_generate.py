"""Agent 7 — Cold Email Generation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from cold_mailer.contracts.common import Citation


class GenerationInput(BaseModel):
    lead_id: int
    domain: str
    recipient_name: str | None = None
    fit: dict = Field(description="A5 FitOutput.model_dump()")
    profile: dict = Field(description="A6 CandidateProfile.model_dump()")
    touch: int = Field(default=1, ge=1, le=3, description="1=opener, 2/3=follow-ups")
    prior_touches: list[dict] = Field(default_factory=list)


class EmailDraft(BaseModel):
    subject_options: list[str] = Field(min_length=3, max_length=3)
    body: str
    linkedin_note: str | None = Field(default=None, max_length=300)
    citations: list[Citation] = Field(default_factory=list)


class GenerationOutput(BaseModel):
    lead_id: int
    touch: int
    draft: EmailDraft
    prompt_version: str
    model: str
