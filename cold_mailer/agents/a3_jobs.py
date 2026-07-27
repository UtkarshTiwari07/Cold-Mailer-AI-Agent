"""Agent 3 — Job Discovery (gap-filled, see contracts/a3_jobs.py).

ATS JSON APIs first — free, exact, zero hallucination surface. Only when
none of the six match does this fall back to crawling the careers page and
asking an LLM to extract postings from unstructured text, which is the one
place in this agent where a model can genuinely invent a job that doesn't
exist — flagged with lower confidence and `source="careers_crawl"` so
downstream agents can weight it accordingly.
"""

from __future__ import annotations

from cold_mailer.agents.research_common import crawl_and_store, get_or_create_company
from cold_mailer.contracts.a3_jobs import JobDiscoveryOutput
from cold_mailer.contracts.common import Confidence, JobPosting
from cold_mailer.core.db import acquire
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.core.prompts import PROMPT_VERSION
from cold_mailer.providers.ats.base import is_engineering
from cold_mailer.providers.ats.detect import detect_and_fetch

log = get_logger(component="a3_jobs", agent="A3")

_CAREERS_LLM_SYSTEM_PROMPT = """You extract job postings from a crawled careers-page's raw text.
Only list postings that literally appear in the text with a real title. If the text has no
recognizable job listings (e.g. it's just a marketing page linking out to an external ATS you
don't have access to), return an empty list rather than guessing — this feeds outreach that will
be visibly wrong if a listed job doesn't actually exist."""


class _CareersExtraction(JobDiscoveryOutput):
    pass


def _derive_hiring_priorities(postings: list[JobPosting]) -> list[str]:
    """Free, deterministic signal instead of an LLM call for the common
    (ATS-matched) path: which departments/functions show up most often
    among current openings."""
    counts: dict[str, int] = {}
    for p in postings:
        key = p.department or "General"
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [f"{dept} ({count} open role{'s' if count != 1 else ''})" for dept, count in ranked[:5]]


async def _stub_jobs(domain: str) -> JobDiscoveryOutput:
    return JobDiscoveryOutput(domain=domain, source="none", confidence=Confidence.low)


async def find_jobs(domain: str, careers_url: str | None = None) -> JobDiscoveryOutput:
    company_id = await get_or_create_company(domain)

    from cold_mailer.pipeline.state_machine import record_stage_run

    match = await detect_and_fetch(domain, careers_url)
    if match:
        engineering = [p for p in match.postings if is_engineering(p)]
        output = JobDiscoveryOutput(
            domain=domain, ats_type=match.ats_type, ats_token=match.token,
            postings=match.postings, engineering_postings=engineering,
            hiring_priorities=_derive_hiring_priorities(match.postings),
            source="ats_api", confidence=Confidence.high,
        )
        # Deterministic path, no LLM call — logged with model=None/cost=0 so
        # stage_runs still shows A3 ran for this company, just for free.
        await record_stage_run(
            subject_type="company", subject_id=company_id, agent="A3", status="ok",
            confidence=1.0, cost_usd=0.0,
        )
    else:
        crawled = await crawl_and_store(company_id, careers_url or f"https://{domain}/careers")
        if crawled is None or len(crawled["text"]) < 200:
            output = JobDiscoveryOutput(domain=domain, source="none", confidence=Confidence.low)
            await record_stage_run(
                subject_type="company", subject_id=company_id, agent="A3", status="ok",
                confidence=0.0, cost_usd=0.0,
            )
        else:
            result = await complete_structured(
                tier="flash",
                output_type=_CareersExtraction,
                system_prompt=_CAREERS_LLM_SYSTEM_PROMPT,
                user_prompt=f"Domain: {domain}\n\nCareers page text:\n{crawled['text']}",
                schema_version=PROMPT_VERSION,
                stub_factory=lambda: _CareersExtraction(domain=domain, source="careers_crawl", confidence=Confidence.low),
            )
            output = result.value
            output.domain = domain
            output.source = "careers_crawl"
            output.engineering_postings = [p for p in output.postings if is_engineering(p)]

            await record_stage_run(
                subject_type="company", subject_id=company_id, agent="A3", status="ok",
                model=result.model, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                cost_usd=result.cost_usd, latency_ms=result.latency_ms,
            )

    async with acquire() as conn:
        await conn.execute(
            "UPDATE companies SET ats_type = $1, ats_token = $2, updated_at = now() WHERE id = $3",
            output.ats_type, output.ats_token, company_id,
        )
        for job in output.postings:
            await conn.execute(
                """
                INSERT INTO jobs (company_id, ats_type, ats_job_id, title, location, department, url,
                                  description, posted_at, raw)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                ON CONFLICT (company_id, ats_job_id) DO UPDATE SET
                    title = EXCLUDED.title, location = EXCLUDED.location,
                    department = EXCLUDED.department, url = EXCLUDED.url,
                    description = EXCLUDED.description, posted_at = EXCLUDED.posted_at,
                    fetched_at = now()
                """,
                company_id, job.ats_type, job.ats_job_id, job.title, job.location, job.department,
                job.url, job.description, job.posted_at, job.model_dump_json(),
            )

    log.info(
        "a3.jobs_found", domain=domain, company_id=company_id, source=output.source,
        total=len(output.postings), engineering=len(output.engineering_postings),
    )
    return output
