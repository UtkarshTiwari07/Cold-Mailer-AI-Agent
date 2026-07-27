"""Agent 4 — Company Classification & Relevance Scoring."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cold_mailer.contracts.common import Confidence


class CompanyCategory(str, Enum):
    faang = "FAANG"
    mnc = "MNC"
    enterprise_saas = "Enterprise SaaS"
    deep_tech = "Deep Tech"
    ai_startup = "AI Startup"
    healthcare = "Healthcare"
    fintech = "FinTech"
    cybersecurity = "Cybersecurity"
    consultancy = "Consultancy"
    indian_startup = "Indian Startup"
    us_startup = "US Startup"
    series_a = "Series A"
    series_b = "Series B"
    bootstrapped = "Bootstrapped"
    service_company = "Service Company"
    research_lab = "Research Lab"
    recruiting_agency = "Recruiting Agency"  # not in the brief's list, but
    # essential: the source data is known to include agency recruiters, who
    # are a fundamentally different (lower-value) outreach target.
    other = "Other"


class RelevanceTier(str, Enum):
    high = "High"
    medium = "Medium"
    low = "Low"


class ClassificationInput(BaseModel):
    domain: str
    discovery: dict
    intel: dict


class ClassificationOutput(BaseModel):
    domain: str
    categories: list[CompanyCategory] = Field(default_factory=list)
    relevance_score: int = Field(ge=0, le=100)
    relevance_tier: RelevanceTier
    rationale: str = Field(description="Why this score — cites specific facts, not vibes")
    is_agency: bool = False
    confidence: Confidence = Confidence.medium
