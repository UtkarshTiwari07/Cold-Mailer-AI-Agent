"""Agent 4 — Company Classification & Relevance Scoring.

Pure reasoning over Agent 1 + Agent 2's output — no new evidence gathering.
Classification and scoring should be derivable from what's already known;
if it isn't, that's itself a (low-confidence) signal, not a reason to go
fetch more pages.
"""

from __future__ import annotations

from cold_mailer.agents.a1_discovery import CONFIDENCE_SCORE
from cold_mailer.agents.research_common import get_or_create_company
from cold_mailer.contracts.a4_classify import ClassificationOutput, RelevanceTier
from cold_mailer.contracts.common import Confidence
from cold_mailer.core.db import acquire
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.core.prompts import PROMPT_VERSION, load_prompt

log = get_logger(component="a4_classify", agent="A4")


def _stub_classification(domain: str) -> ClassificationOutput:
    return ClassificationOutput(
        domain=domain, categories=[], relevance_score=50, relevance_tier=RelevanceTier.medium,
        rationale="Stub mode — no real classification performed.", confidence=Confidence.low,
    )


async def classify(domain: str, discovery: dict, intel: dict) -> ClassificationOutput:
    company_id = await get_or_create_company(domain)

    user_prompt = (
        f"Company domain: {domain}\n\nAgent 1 discovery:\n{discovery}\n\nAgent 2 intelligence:\n{intel}"
    )

    result = await complete_structured(
        tier="flash",
        output_type=ClassificationOutput,
        system_prompt=load_prompt("a4_classify"),
        user_prompt=user_prompt,
        schema_version=PROMPT_VERSION,
        stub_factory=lambda: _stub_classification(domain),
    )
    output = result.value
    output.domain = domain

    # Belt-and-suspenders consistency check between the numeric score and
    # the categorical tier — a model can drift these apart even when each
    # individually looks reasonable, and stale/inconsistent tiers would
    # silently mis-route leads in the approval queue's filters.
    if output.relevance_score >= 70:
        output.relevance_tier = RelevanceTier.high
    elif output.relevance_score >= 40:
        output.relevance_tier = RelevanceTier.medium
    else:
        output.relevance_tier = RelevanceTier.low

    async with acquire() as conn:
        await conn.execute(
            "UPDATE companies SET classification = $1::jsonb, status = 'researched', "
            "last_researched_at = now(), updated_at = now() WHERE id = $2",
            output.model_dump_json(), company_id,
        )

    from cold_mailer.pipeline.state_machine import record_stage_run

    await record_stage_run(
        subject_type="company", subject_id=company_id, agent="A4", status="ok",
        confidence=CONFIDENCE_SCORE[output.confidence], model=result.model,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
    log.info(
        "a4.classified", domain=domain, company_id=company_id,
        score=output.relevance_score, tier=output.relevance_tier.value, is_agency=output.is_agency,
    )
    return output
