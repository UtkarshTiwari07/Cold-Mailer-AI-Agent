"""Agent 3 — Job Discovery (gap-filled: the brief's high-level goal calls for
"find current openings" and "understand hiring priorities" but never numbers
this agent). ATS JSON APIs are tried before any crawling — free, exact, and
they remove the biggest hallucination surface in the whole pipeline: made-up
job titles.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cold_mailer.contracts.common import Confidence, JobPosting


class JobDiscoveryInput(BaseModel):
    domain: str
    careers_url: str | None = None
    ats_hint: str | None = None  # from a prior company row, if already known


class JobDiscoveryOutput(BaseModel):
    domain: str
    ats_type: str | None = None
    ats_token: str | None = None
    postings: list[JobPosting] = Field(default_factory=list)
    engineering_postings: list[JobPosting] = Field(default_factory=list)
    hiring_priorities: list[str] = Field(default_factory=list)
    source: str = Field(description="'ats_api' | 'careers_crawl' | 'none'")
    confidence: Confidence = Confidence.medium
