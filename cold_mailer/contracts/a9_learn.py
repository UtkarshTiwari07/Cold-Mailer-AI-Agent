"""Agent 9 — Learning."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VariantStats(BaseModel):
    key: str  # e.g. subject line, prompt_version, category
    sent: int
    opened: int | None = None  # not tracked (decision: replies+bounces only); kept nullable
    replied: int
    bounced: int
    reply_rate: float
    bounce_rate: float


class LearningReport(BaseModel):
    generated_at: str
    overall_reply_rate: float
    overall_bounce_rate: float
    by_subject_line: list[VariantStats] = Field(default_factory=list)
    by_prompt_version: list[VariantStats] = Field(default_factory=list)
    by_category: list[VariantStats] = Field(default_factory=list)
    by_relevance_tier: list[VariantStats] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
