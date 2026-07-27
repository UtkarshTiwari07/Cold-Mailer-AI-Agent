"""Agent 2 — Deep Company Intelligence."""

from __future__ import annotations

from pydantic import BaseModel, Field

from cold_mailer.contracts.common import Confidence, ResearchFact


class IntelInput(BaseModel):
    domain: str
    discovery: dict = Field(description="DiscoveryOutput.model_dump() from Agent 1")


class IntelOutput(BaseModel):
    domain: str
    problem_solved: str | None = None
    business_model: str | None = None
    competitive_landscape: list[str] = Field(default_factory=list)
    engineering_culture_notes: str | None = None
    recent_launches: list[str] = Field(default_factory=list)
    ai_initiatives: list[str] = Field(default_factory=list)
    hiring_velocity: str | None = None
    leadership: list[str] = Field(default_factory=list)
    current_priorities: list[str] = Field(default_factory=list)

    likely_engineering_challenges: list[ResearchFact] = Field(default_factory=list)
    potential_pain_points_for_me: list[ResearchFact] = Field(default_factory=list)

    confidence: Confidence = Confidence.medium
    evidence_ids: list[int] = Field(default_factory=list)
