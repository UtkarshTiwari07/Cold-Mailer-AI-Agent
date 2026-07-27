"""Lever public postings API.

Contract verified live: `GET https://api.lever.co/v0/postings/{token}?mode=json`
returns a bare JSON array (not wrapped in an object) of
`{id, text, categories: {team, location, department}, hostedUrl, createdAt,
descriptionPlain}`. A token with no board returns `[]`, not a 404 — Lever
never distinguishes "wrong token" from "no jobs" at this endpoint.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import httpx

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.ats.base import ATSClient

log = get_logger(component="ats.lever")


class LeverClient(ATSClient):
    name = "lever"

    @network_retry(max_attempts=3)
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        url = f"https://api.lever.co/v0/postings/{token}?mode=json"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.info("ats.lever.not_found", token=token, status=resp.status_code)
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        if not isinstance(data, list):
            return []

        postings: list[JobPosting] = []
        for job in data:
            categories = job.get("categories") or {}
            posted_at = None
            if job.get("createdAt"):
                with contextlib.suppress(ValueError, OSError):
                    posted_at = datetime.fromtimestamp(int(job["createdAt"]) / 1000, tz=UTC)
            postings.append(
                JobPosting(
                    ats_type=self.name,
                    ats_job_id=str(job.get("id")),
                    title=job.get("text", ""),
                    location=categories.get("location"),
                    department=categories.get("team") or categories.get("department"),
                    url=job.get("hostedUrl"),
                    description=(job.get("descriptionPlain") or "")[:2000] or None,
                    posted_at=posted_at,
                )
            )
        return postings
