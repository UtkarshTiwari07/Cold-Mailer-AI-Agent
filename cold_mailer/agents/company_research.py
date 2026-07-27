"""Orchestrates A1 -> A2 -> A3 -> A4 for one company, then fans out a
fit-synthesis task to every lead waiting on that company's research.

Registered as the `research_company` task handler — this is the one task
kind that turns a company row from `new` into `researched`, and it is the
concrete realization of "research runs once per company, not once per
lead": however many recruiter emails share this domain, this function runs
exactly once for all of them.
"""

from __future__ import annotations

from cold_mailer.agents.a1_discovery import discover
from cold_mailer.agents.a2_intel import analyze
from cold_mailer.agents.a3_jobs import find_jobs
from cold_mailer.agents.a4_classify import classify
from cold_mailer.core.db import acquire
from cold_mailer.core.logging import get_logger
from cold_mailer.pipeline.stages import task_handler
from cold_mailer.pipeline.state_machine import Task, enqueue_task

log = get_logger(component="company_research")


async def research_company(domain: str) -> dict:
    discovery = await discover(domain)
    intel = await analyze(domain, discovery.model_dump(mode="json"))
    await find_jobs(domain, discovery.careers_url)
    classification = await classify(
        domain, discovery.model_dump(mode="json"), intel.model_dump(mode="json")
    )

    async with acquire() as conn:
        company_row = await conn.fetchrow("SELECT id FROM companies WHERE domain = $1", domain)
        company_id = company_row["id"]
        lead_rows = await conn.fetch(
            "SELECT id FROM leads WHERE company_id = $1 AND status IN ('validated', 'researching')",
            company_id,
        )

    for lead in lead_rows:
        await enqueue_task(
            "synthesize_fit", "lead", lead["id"], {"domain": domain},
            dedupe_key=f"synthesize_fit:{lead['id']}",
        )

    log.info(
        "company_research.done", domain=domain, company_id=company_id,
        leads_fanned_out=len(lead_rows), relevance_score=classification.relevance_score,
        is_agency=classification.is_agency,
    )
    return {
        "domain": domain, "company_id": company_id,
        "relevance_score": classification.relevance_score, "is_agency": classification.is_agency,
    }


@task_handler("research_company")
async def _handle_research_company(task: Task) -> None:
    domain = task.payload.get("domain")
    if not domain:
        raise ValueError(f"research_company task {task.id} missing 'domain' in payload")
    await research_company(domain)
