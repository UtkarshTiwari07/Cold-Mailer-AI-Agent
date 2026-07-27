"""Agent 5 — Fit & Angle Synthesis (gap-filled: bridges Agent 4's company
score and Agent 6's candidate profile into the single strongest, specific
angle Agent 7 will write from. Without this, Agent 7 either re-derives fit
reasoning inside the writing prompt — expensive and inconsistent — or writes
generic copy that doesn't actually connect the two sides).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cold_mailer.contracts.common import Confidence


class Hook(BaseModel):
    text: str
    supporting_project: str | None = None
    supporting_evidence_id: int | None = None
    strength: int = Field(ge=1, le=5)


class FitInput(BaseModel):
    domain: str
    discovery: dict
    intel: dict
    classification: dict
    jobs: dict
    profile: dict = Field(description="A6 CandidateProfile.model_dump()")


class FitOutput(BaseModel):
    domain: str
    lead_id: int | None = None
    hooks: list[Hook] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="Honest mismatches, not hidden")
    strongest_angle: str
    matched_job_ids: list[int] = Field(default_factory=list)
    recommended_touch_count: int = Field(default=3, ge=1, le=3)
    confidence: Confidence = Confidence.medium
