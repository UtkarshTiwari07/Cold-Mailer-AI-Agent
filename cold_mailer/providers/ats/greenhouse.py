"""Greenhouse public job board API.

Contract verified live: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
returns `{"jobs": [{id, title, updated_at, absolute_url, location: {name},
departments: [{name}], content: "<html>"}]}`. Confirmed against gitlab's
public board (131KB, real listings) while building this client.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime

import httpx

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.ats.base import ATSClient

log = get_logger(component="ats.greenhouse")

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str | None, limit: int = 2000) -> str | None:
    if not html:
        return None
    text = _TAG_RE.sub(" ", html)
    text = " ".join(text.split())
    return text[:limit]


class GreenhouseClient(ATSClient):
    name = "greenhouse"

    @network_retry(max_attempts=3)
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.info("ats.greenhouse.not_found", token=token, status=resp.status_code)
            return []
        data = resp.json()
        postings: list[JobPosting] = []
        for job in data.get("jobs", []):
            departments = job.get("departments") or []
            posted_at = None
            if job.get("updated_at"):
                with contextlib.suppress(ValueError):
                    posted_at = datetime.fromisoformat(job["updated_at"].replace("Z", "+00:00"))
            postings.append(
                JobPosting(
                    ats_type=self.name,
                    ats_job_id=str(job["id"]),
                    title=job.get("title", ""),
                    location=(job.get("location") or {}).get("name"),
                    department=departments[0]["name"] if departments else None,
                    url=job.get("absolute_url"),
                    description=_strip_html(job.get("content")),
                    posted_at=posted_at,
                )
            )
        return postings
