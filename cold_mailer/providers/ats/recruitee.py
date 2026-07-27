"""Recruitee public offers API.

Contract verified live against personio.recruitee.com: `GET
https://{token}.recruitee.com/api/offers/` returns `{"offers": [{id, title,
slug, careers_url, department, location, remote, description, created_at}]}`.
A sample response is saved at `tests/fixtures/recruitee_personio.json`.
"""

from __future__ import annotations

import contextlib
from datetime import datetime

import httpx

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.ats.base import ATSClient

log = get_logger(component="ats.recruitee")


class RecruiteeClient(ATSClient):
    name = "recruitee"

    @network_retry(max_attempts=3)
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        url = f"https://{token}.recruitee.com/api/offers/"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.info("ats.recruitee.not_found", token=token, status=resp.status_code)
            return []
        data = resp.json()
        postings: list[JobPosting] = []
        for job in data.get("offers", []):
            posted_at = None
            if job.get("created_at"):
                with contextlib.suppress(ValueError):
                    posted_at = datetime.strptime(job["created_at"], "%Y-%m-%d %H:%M:%S UTC")
            postings.append(
                JobPosting(
                    ats_type=self.name,
                    ats_job_id=str(job.get("id")),
                    title=job.get("title", ""),
                    location=job.get("location"),
                    department=job.get("department"),
                    url=job.get("careers_url"),
                    description=(job.get("description") or "")[:2000] or None,
                    posted_at=posted_at,
                )
            )
        return postings
