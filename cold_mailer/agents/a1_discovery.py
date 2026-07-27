"""Agent 1 — Company Discovery.

Crawls the company's own site plus a couple of targeted searches, stores
everything as evidence, then asks an LLM to distill it into a structured
profile — grounded, with every non-trivial claim traceable to an evidence
id (see `prompts/a1_discovery.md`).
"""

from __future__ import annotations

from cold_mailer.agents.research_common import (
    crawl_and_store,
    format_evidence_for_prompt,
    get_evidence,
    get_or_create_company,
    search_and_store,
)
from cold_mailer.contracts.a1_discovery import DiscoveryOutput
from cold_mailer.contracts.common import CompanySize, Confidence
from cold_mailer.core.db import acquire
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.core.prompts import PROMPT_VERSION, load_prompt

log = get_logger(component="a1_discovery", agent="A1")

CONFIDENCE_SCORE = {Confidence.high: 0.9, Confidence.medium: 0.6, Confidence.low: 0.3}

_CAREERS_PATH_CANDIDATES = ("/careers", "/jobs", "/about")


def _stub_discovery(domain: str) -> DiscoveryOutput:
    name = domain.split(".")[0].capitalize()
    return DiscoveryOutput(
        domain=domain, name=name, website=f"https://{domain}",
        industry="Software", products=[f"{name} Platform"], company_size=CompanySize.medium,
        confidence=Confidence.low,
    )


async def _gather_evidence(company_id: int, domain: str) -> None:
    await crawl_and_store(company_id, f"https://{domain}")
    for path in _CAREERS_PATH_CANDIDATES:
        found = await crawl_and_store(company_id, f"https://{domain}{path}")
        if found:
            break
    await search_and_store(company_id, f"{domain} company about funding size industry", max_results=5)
    await search_and_store(company_id, f"{domain} engineering blog tech stack products", max_results=5)


async def discover(domain: str, seed_email: str | None = None) -> DiscoveryOutput:
    company_id = await get_or_create_company(domain)
    await _gather_evidence(company_id, domain)

    evidence = await get_evidence(company_id)
    user_prompt = f"Company domain: {domain}\n\nEvidence:\n{format_evidence_for_prompt(evidence)}"

    result = await complete_structured(
        tier="flash",
        output_type=DiscoveryOutput,
        system_prompt=load_prompt("a1_discovery"),
        user_prompt=user_prompt,
        schema_version=PROMPT_VERSION,
        stub_factory=lambda: _stub_discovery(domain),
    )
    output = result.value
    output.domain = domain

    async with acquire() as conn:
        await conn.execute(
            "UPDATE companies SET profile = $1::jsonb, research_confidence = $2, updated_at = now() WHERE id = $3",
            output.model_dump_json(), CONFIDENCE_SCORE[output.confidence], company_id,
        )

    from cold_mailer.pipeline.state_machine import record_stage_run

    await record_stage_run(
        subject_type="company", subject_id=company_id, agent="A1", status="ok",
        confidence=CONFIDENCE_SCORE[output.confidence], model=result.model,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
    log.info("a1.discovered", domain=domain, company_id=company_id, confidence=output.confidence.value)
    return output
