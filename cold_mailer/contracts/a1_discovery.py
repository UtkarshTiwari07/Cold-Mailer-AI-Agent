"""Agent 1 — Company Discovery."""

from __future__ import annotations

from pydantic import BaseModel, Field

from cold_mailer.contracts.common import CompanySize, Confidence


class DiscoveryInput(BaseModel):
    domain: str
    seed_email: str | None = None  # the recruiter email that produced this domain


class DiscoveryOutput(BaseModel):
    domain: str
    name: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    careers_url: str | None = None
    engineering_blog_url: str | None = None
    github_org: str | None = None
    crunchbase_url: str | None = None
    yc_url: str | None = None
    product_hunt_url: str | None = None

    industry: str | None = None
    products: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    mission: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    open_source_projects: list[str] = Field(default_factory=list)

    company_size: CompanySize = CompanySize.unknown
    recent_funding: str | None = None
    recent_news: list[str] = Field(default_factory=list)
    hiring_trend: str | None = None
    ai_adoption_notes: str | None = None

    confidence: Confidence = Confidence.medium
    evidence_ids: list[int] = Field(default_factory=list)
