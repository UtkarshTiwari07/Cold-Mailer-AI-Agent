"""Shared value types used across multiple agent contracts.

Every agent module in `contracts/` defines an `Input` and `Output` model.
Those are the only surface an agent implementation is allowed to depend on
from the outside — swap `agents/a4_classify.py` for a different
implementation and nothing else in the pipeline needs to change, as long as
it still produces `ClassificationOutput`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Confidence(str, Enum):
    """Every agent reports how sure it is. Low-confidence output routes to
    human review instead of silently flowing downstream."""

    high = "high"
    medium = "medium"
    low = "low"


class Citation(BaseModel):
    """A pointer into `evidence` — never a bare claim. A7's grounding check
    (quality/grounding.py) rejects any generated sentence about the company
    that doesn't trace back to at least one of these."""

    evidence_id: int | None = None
    url: str | None = None
    quote: str = Field(..., description="The exact snippet the claim is based on")


class ResearchFact(BaseModel):
    claim: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence = Confidence.medium


class JobPosting(BaseModel):
    ats_type: str | None = None
    ats_job_id: str
    title: str
    location: str | None = None
    department: str | None = None
    url: str | None = None
    description: str | None = None
    posted_at: datetime | None = None


class CompanySize(str, Enum):
    unknown = "unknown"
    micro = "1-10"
    small = "11-50"
    medium = "51-200"
    large = "201-1000"
    xlarge = "1001-5000"
    enterprise = "5000+"


class AgentError(BaseModel):
    """Returned instead of raising, so a failed stage is a normal, loggable
    value rather than an exception that has to be reconstructed from a
    traceback later. The pipeline decides retry/skip/fail from this."""

    agent: str
    message: str
    retryable: bool = True
    raw: str | None = None
