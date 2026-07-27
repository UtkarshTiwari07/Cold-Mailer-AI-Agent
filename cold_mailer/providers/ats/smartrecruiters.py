"""SmartRecruiters public postings API.

Contract verified live against Visa's real board: `GET
https://api.smartrecruiters.com/v1/companies/{token}/postings` returns
`{offset, limit, totalFound, content: [{id, name, uuid, releasedDate,
location: {city, region, country, fullLocation}, department: {label},
function: {label}}]}`. A sample response is saved at
`tests/fixtures/smartrecruiters_visa.json`.
"""

from __future__ import annotations

import contextlib
from datetime import datetime

import httpx

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.ats.base import ATSClient

log = get_logger(component="ats.smartrecruiters")


class SmartRecruitersClient(ATSClient):
    name = "smartrecruiters"

    @network_retry(max_attempts=3)
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.info("ats.smartrecruiters.not_found", token=token, status=resp.status_code)
            return []
        data = resp.json()
        postings: list[JobPosting] = []
        for job in data.get("content", []):
            location = job.get("location") or {}
            department = job.get("department") or {}
            posted_at = None
            if job.get("releasedDate"):
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(job["releasedDate"].replace("Z", "+00:00"))
            postings.append(
                JobPosting(
                    ats_type=self.name,
                    ats_job_id=str(job["id"]),
                    title=job.get("name", ""),
                    location=location.get("fullLocation"),
                    department=department.get("label"),
                    url=f"https://jobs.smartrecruiters.com/{token}/{job['id']}",
                    description=None,
                    posted_at=posted_at,
                )
            )
        return postings
