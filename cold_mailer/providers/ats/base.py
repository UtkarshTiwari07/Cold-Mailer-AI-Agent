"""ATS (Applicant Tracking System) job-board clients.

Every ATS listed here exposes a public, unauthenticated JSON endpoint for
its customers' job boards — no scraping, no hallucination surface, exact
titles and locations. Job discovery (Agent 3) always tries these first and
only falls back to crawling a company's careers page when none match, which
is also why detection (`detect.py`) matters: guessing the wrong ATS just
means an empty result, handled the same as "no ATS" — never a crash.

Contracts verified live against real company job boards while building this
(Greenhouse: gitlab, SmartRecruiters: Visa, Recruitee: personio); Workable
against stripe/canva (empty listings, but confirmed response shape); Lever
against a token with live postings; Ashby's public GraphQL API is
undocumented and no live example could be confirmed with the org names
tried, so it's implemented against the widely-used community-reverse-
engineered schema and treated as best-effort — same graceful-empty fallback
as everything else if the schema has since drifted.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from cold_mailer.contracts.common import JobPosting


class ATSClient(ABC):
    name: str

    @abstractmethod
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        """Returns an empty list (never raises) if the token doesn't exist
        on this ATS or the board has no listings — callers can't tell fetch
        failure from "no jobs" and shouldn't need to; A3 tries the next ATS
        or falls back to crawling either way."""


def is_engineering(posting: JobPosting) -> bool:
    """Cheap, deterministic pre-filter so Agent 4/5 spend LLM tokens on the
    handful of engineering-relevant postings, not the whole board."""
    haystack = f"{posting.title} {posting.department or ''}".lower()
    return any(
        kw in haystack
        for kw in (
            "engineer", "developer", "swe", "software", "backend", "frontend",
            "full stack", "full-stack", "devops", "sre", "platform", "infra",
            "data scientist", "machine learning", "ml ", "ai ", "architect",
            "technical lead", "tech lead",
        )
    )
