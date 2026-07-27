"""Ashby public job board GraphQL API — best-effort.

Ashby does not publish this API; the query below is the widely-used
community-reverse-engineered contract. No live org name tried while building
this returned a populated board (`jobBoard: null` for every candidate org),
so it is unverified against real data and may have drifted. It is wired in
with the same graceful-empty-list behavior as every other ATS client:
`fetch_jobs` never raises, so a schema drift here degrades to "Ashby found
nothing" and Agent 3 falls through to careers-page crawling — it does not
break the pipeline. If this becomes load-bearing, prioritize re-verifying
the schema against a confirmed Ashby customer's board slug first.
"""

from __future__ import annotations

import httpx

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.ats.base import ATSClient

log = get_logger(component="ats.ashby")

_QUERY = """
query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
    teams { id name }
    jobPostings { id title teamId locationName employmentType isListed }
  }
}
"""


class AshbyClient(ATSClient):
    name = "ashby"

    @network_retry(max_attempts=3)
    async def fetch_jobs(self, token: str) -> list[JobPosting]:
        body = {
            "operationName": "ApiJobBoardWithTeams",
            "variables": {"organizationHostedJobsPageName": token},
            "query": _QUERY,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams", json=body
            )
        if resp.status_code != 200:
            log.info("ats.ashby.not_found", token=token, status=resp.status_code)
            return []
        data = resp.json()
        job_board = (data.get("data") or {}).get("jobBoard")
        if not job_board:
            return []

        postings: list[JobPosting] = []
        for job in job_board.get("jobPostings", []):
            if job.get("isListed") is False:
                continue
            postings.append(
                JobPosting(
                    ats_type=self.name,
                    ats_job_id=str(job["id"]),
                    title=job.get("title", ""),
                    location=job.get("locationName"),
                    department=None,
                    url=f"https://jobs.ashbyhq.com/{token}/{job['id']}",
                    description=None,
                    posted_at=None,
                )
            )
        return postings
