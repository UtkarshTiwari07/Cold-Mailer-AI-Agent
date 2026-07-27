"""Agent 2 — Deep Company Intelligence.

Reuses A1's evidence rather than re-crawling (see `research_common.py`
docstring), adding only a couple of targeted searches aimed at what A1
doesn't usually surface: competitors and recent product launches.
"""

from __future__ import annotations

from cold_mailer.agents.a1_discovery import CONFIDENCE_SCORE
from cold_mailer.agents.research_common import (
    format_evidence_for_prompt,
    get_evidence,
    get_or_create_company,
    search_and_store,
)
from cold_mailer.contracts.a2_intel import IntelOutput
from cold_mailer.contracts.common import Confidence
from cold_mailer.core.db import acquire
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.core.prompts import PROMPT_VERSION, load_prompt

log = get_logger(component="a2_intel", agent="A2")


def _stub_intel(domain: str) -> IntelOutput:
    return IntelOutput(domain=domain, confidence=Confidence.low)


async def analyze(domain: str, discovery: dict) -> IntelOutput:
    company_id = await get_or_create_company(domain)

    await search_and_store(company_id, f"{domain} competitors alternatives", max_results=4)
    await search_and_store(company_id, f"{domain} product launch 2026 announcement", max_results=4)

    evidence = await get_evidence(company_id)
    user_prompt = (
        f"Company domain: {domain}\n\n"
        f"Agent 1 discovery output:\n{discovery}\n\n"
        f"Evidence:\n{format_evidence_for_prompt(evidence)}"
    )

    result = await complete_structured(
        tier="flash",
        output_type=IntelOutput,
        system_prompt=load_prompt("a2_intel"),
        user_prompt=user_prompt,
        schema_version=PROMPT_VERSION,
        stub_factory=lambda: _stub_intel(domain),
    )
    output = result.value
    output.domain = domain

    async with acquire() as conn:
        await conn.execute(
            "UPDATE companies SET intel = $1::jsonb, updated_at = now() WHERE id = $2",
            output.model_dump_json(), company_id,
        )

    from cold_mailer.pipeline.state_machine import record_stage_run

    await record_stage_run(
        subject_type="company", subject_id=company_id, agent="A2", status="ok",
        confidence=CONFIDENCE_SCORE[output.confidence], model=result.model,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
    log.info("a2.analyzed", domain=domain, company_id=company_id, confidence=output.confidence.value)
    return output
