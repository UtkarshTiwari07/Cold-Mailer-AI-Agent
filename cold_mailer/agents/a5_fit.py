"""Agent 5 — Fit & Angle Synthesis (gap-filled, see contracts/a5_fit.py).

The candidate profile goes into the STABLE system prompt (via
`profile_system_prefix()`), not the per-lead user prompt — it's identical
on every call this run makes, so that's what makes DeepSeek's prefix cache
actually pay off across leads. Runs on the `pro` tier: this is a judgment
call (which hook is genuinely strong vs. a stretch), not extraction, and
it's the one output Agent 7 depends on entirely for what to write.
"""

from __future__ import annotations

import json

from cold_mailer.agents.a6_profile import profile_system_prefix
from cold_mailer.contracts.a5_fit import FitOutput
from cold_mailer.contracts.common import Confidence
from cold_mailer.core.db import acquire
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.core.prompts import PROMPT_VERSION, load_prompt
from cold_mailer.pipeline.stages import task_handler
from cold_mailer.pipeline.state_machine import Task, enqueue_task, record_stage_run

log = get_logger(component="a5_fit", agent="A5")

CONFIDENCE_SCORE = {Confidence.high: 0.9, Confidence.medium: 0.6, Confidence.low: 0.3}


def _stub_fit(domain: str) -> FitOutput:
    return FitOutput(
        domain=domain, strongest_angle="Stub mode — no real fit analysis performed.",
        confidence=Confidence.low,
    )


async def _load_company_context(domain: str) -> tuple[int, dict, dict, dict, list[dict]]:
    async with acquire() as conn:
        company = await conn.fetchrow(
            "SELECT id, profile, intel, classification FROM companies WHERE domain = $1", domain
        )
        if company is None:
            raise ValueError(f"No researched company found for domain={domain!r}")
        jobs = await conn.fetch(
            "SELECT id, title, department, location, url FROM jobs WHERE company_id = $1 LIMIT 50",
            company["id"],
        )
    discovery = json.loads(company["profile"]) if company["profile"] else {}
    intel = json.loads(company["intel"]) if company["intel"] else {}
    classification = json.loads(company["classification"]) if company["classification"] else {}
    return company["id"], discovery, intel, classification, [dict(j) for j in jobs]


async def synthesize_fit(domain: str, lead_id: int) -> FitOutput:
    company_id, discovery, intel, classification, jobs = await _load_company_context(domain)

    system_prompt = load_prompt("a5_fit") + "\n\n" + profile_system_prefix()
    user_prompt = (
        f"Company domain: {domain}\n\n"
        f"Discovery:\n{discovery}\n\nIntelligence:\n{intel}\n\n"
        f"Classification:\n{classification}\n\nOpen jobs:\n{jobs}"
    )

    result = await complete_structured(
        tier="pro",
        output_type=FitOutput,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_version=PROMPT_VERSION,
        stub_factory=lambda: _stub_fit(domain),
    )
    output = result.value
    output.domain = domain
    output.lead_id = lead_id

    # fit_analyses.tier has a CHECK constraint on lowercase 'high'/'medium'/'low';
    # ClassificationOutput.relevance_tier is the capitalized display enum
    # ("High"/"Medium"/"Low") — must be lowercased or the insert violates
    # the constraint.
    tier = str(classification.get("relevance_tier", "medium")).lower()

    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fit_analyses
                (lead_id, company_id, score, tier, angle, rationale, hooks, gaps, matched_job_ids)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9)
            ON CONFLICT (lead_id) DO UPDATE SET
                company_id = EXCLUDED.company_id, score = EXCLUDED.score, tier = EXCLUDED.tier,
                angle = EXCLUDED.angle, rationale = EXCLUDED.rationale, hooks = EXCLUDED.hooks,
                gaps = EXCLUDED.gaps, matched_job_ids = EXCLUDED.matched_job_ids
            """,
            lead_id, company_id,
            classification.get("relevance_score", 50), tier,
            output.strongest_angle, classification.get("rationale"),
            json.dumps([h.model_dump(mode="json") for h in output.hooks]),
            json.dumps(output.gaps),
            output.matched_job_ids,
        )
        await conn.execute("UPDATE leads SET status = 'ready', updated_at = now() WHERE id = $1", lead_id)

    await record_stage_run(
        subject_type="lead", subject_id=lead_id, agent="A5", status="ok",
        confidence=CONFIDENCE_SCORE[output.confidence], model=result.model,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )

    await enqueue_task(
        "generate_draft", "lead", lead_id, {"domain": domain, "touch": 1},
        dedupe_key=f"generate_draft:{lead_id}:1",
    )

    log.info("a5.fit_synthesized", domain=domain, lead_id=lead_id, angle=output.strongest_angle[:80])
    return output


@task_handler("synthesize_fit")
async def _handle_synthesize_fit(task: Task) -> None:
    domain = task.payload.get("domain")
    if not domain:
        raise ValueError(f"synthesize_fit task {task.id} missing 'domain' in payload")
    await synthesize_fit(domain, task.subject_id)
