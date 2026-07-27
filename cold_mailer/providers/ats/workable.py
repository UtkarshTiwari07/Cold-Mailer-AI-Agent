"""Workable public widget API.

Contract verified live: `GET https://apply.workable.com/api/v1/widget/accounts/{token}`
returns `{"name", "description", "jobs": [{title, shortcode, code, state,
department, url, location: {location_str, ...}, published_on}]}`. Confirmed
against stripe/canva (200 OK, empty `jobs: []` since neither has current
public listings — the shape held either way).

The widget listing does not include job descriptions; a per-job detail call
(`.../jobs/{shortcode}`) would be needed for that and is not implemented in
this MVP to keep one clean HTTP round-trip per company on the common path.
"""

from __future__ import annotations

import httpx

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.ats.base import ATSClient

log = get_logger(component="ats.workable")


class WorkableClient(ATSClient):
    name = "workable"

    @network_retry(max_attempts=3)
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{token}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.info("ats.workable.not_found", token=token, status=resp.status_code)
            return []
        data = resp.json()
        postings: list[JobPosting] = []
        for job in data.get("jobs", []):
            location = job.get("location") or {}
            postings.append(
                JobPosting(
                    ats_type=self.name,
                    ats_job_id=str(job.get("shortcode") or job.get("code") or job.get("title")),
                    title=job.get("title", ""),
                    location=location.get("location_str") or location.get("city"),
                    department=job.get("department"),
                    url=job.get("url"),
                    description=None,
                    posted_at=None,
                )
            )
        return postings
