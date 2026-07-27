"""Agent 7 — Cold Email Generation, with Agent 7.5's QA guardrail folded in
as a regenerate loop rather than a separate pipeline stage. The brief's
pipeline has no gate between "generate" and "send" — this is that gate: a
draft that fails the deterministic linter or the grounding check gets a
second (and at most third) attempt with the specific failures fed back to
the model, before it's ever allowed to reach the human approval queue.

Runs on the `pro` tier, same reasoning as A5: this is the one output a
human will actually read and a recruiter will actually receive, so it gets
the stronger model. The candidate profile lives in the stable system
prompt (`profile_system_prefix()`), same cache-economics reasoning as A5.
"""

from __future__ import annotations

import json

from cold_mailer.agents.a6_profile import load_profile, profile_system_prefix
from cold_mailer.contracts.a7_generate import EmailDraft, GenerationOutput
from cold_mailer.core.db import acquire
from cold_mailer.core.llm import complete_structured
from cold_mailer.core.logging import get_logger
from cold_mailer.core.prompts import PROMPT_VERSION, load_prompt
from cold_mailer.pipeline.stages import task_handler
from cold_mailer.pipeline.state_machine import Task, record_stage_run
from cold_mailer.quality.grounding import check_grounding
from cold_mailer.quality.linter import lint_draft, lint_passed

log = get_logger(component="a7_generate", agent="A7")

MAX_REGENERATIONS = 2


def _stub_draft() -> EmailDraft:
    return EmailDraft(
        subject_options=["quick question", "reply rates", "hiring ops"],
        body="Stub mode — no real draft generated.",
    )


def _profile_grounding_texts() -> list[str]:
    """A7's grounding check must accept two independent source domains:
    facts about the COMPANY (from `evidence`, gathered by A1/A2) and facts
    about the CANDIDATE (from the profile's own projects/achievements,
    which never touch the `evidence` table since they aren't scraped from
    anywhere — they're first-party). Checking citations only against
    company evidence would flag every true claim about the candidate's own
    work as "unverifiable" and burn regeneration cycles rewriting things
    that were already correct — which is exactly what happened before this
    fix was added.

    Includes each project's `technologies` and the profile's top-level
    `skills` — found missing during a live run where a citation naming a
    project's tech stack ("Go, Redis, Kubernetes") was wrongly flagged
    ungrounded because only description/outcome/achievement text was
    included, not the technology lists themselves."""
    profile = load_profile()
    texts = [p.description for p in profile.projects] + [p.outcome or "" for p in profile.projects]
    texts += [", ".join(p.technologies) for p in profile.projects]
    texts += profile.achievements
    texts.append(profile.headline)
    texts.append(", ".join(profile.skills))
    return [t for t in texts if t]


async def _load_fit_and_evidence(lead_id: int) -> tuple[dict, list[str], str | None]:
    async with acquire() as conn:
        fit = await conn.fetchrow(
            "SELECT company_id, score, tier, angle, rationale, hooks, gaps FROM fit_analyses WHERE lead_id = $1",
            lead_id,
        )
        if fit is None:
            raise ValueError(f"No fit analysis found for lead_id={lead_id}")
        lead = await conn.fetchrow("SELECT display_name FROM leads WHERE id = $1", lead_id)
        evidence_rows = await conn.fetch(
            "SELECT text FROM evidence WHERE company_id = $1 LIMIT 50", fit["company_id"]
        )

    fit_dict = {
        "score": fit["score"], "tier": fit["tier"], "angle": fit["angle"],
        "rationale": fit["rationale"],
        "hooks": json.loads(fit["hooks"]) if fit["hooks"] else [],
        "gaps": json.loads(fit["gaps"]) if fit["gaps"] else [],
    }
    evidence_texts = [r["text"] for r in evidence_rows] + _profile_grounding_texts()
    recipient_name = lead["display_name"] if lead else None
    return fit_dict, evidence_texts, recipient_name


async def _generate_once(
    system_prompt: str, user_prompt: str, feedback: str | None
) -> tuple[EmailDraft, str, int, int, float, int]:
    full_user_prompt = user_prompt if not feedback else f"{user_prompt}\n\n---\n{feedback}"
    result = await complete_structured(
        tier="pro",
        output_type=EmailDraft,
        system_prompt=system_prompt,
        user_prompt=full_user_prompt,
        schema_version=PROMPT_VERSION,
        stub_factory=_stub_draft,
    )
    return (
        result.value, result.model, result.tokens_in, result.tokens_out, result.cost_usd, result.latency_ms
    )


async def generate_draft(domain: str, lead_id: int, touch: int = 1) -> GenerationOutput:
    fit_dict, evidence_texts, recipient_name = await _load_fit_and_evidence(lead_id)

    system_prompt = load_prompt("a7_generate") + "\n\n" + profile_system_prefix()
    user_prompt = (
        f"Company domain: {domain}\nRecipient name: {recipient_name or 'unknown'}\nTouch: {touch} of 3\n\n"
        f"Fit analysis:\n{fit_dict}"
    )

    feedback: str | None = None
    draft: EmailDraft | None = None
    model = tokens_in = tokens_out = cost = latency = None
    lint_findings = []
    grounded = False
    regen_count = 0

    for attempt in range(MAX_REGENERATIONS + 1):
        draft, model, tokens_in, tokens_out, cost, latency = await _generate_once(
            system_prompt, user_prompt, feedback
        )
        lint_findings = lint_draft(draft)
        passed = lint_passed(lint_findings)
        grounded, ungrounded_claims = check_grounding(draft, evidence_texts)

        if passed and grounded:
            break

        regen_count = attempt + 1
        issues = "; ".join(f"{f.rule}: {f.detail}" for f in lint_findings if f.severity == "error")
        if ungrounded_claims:
            issues += "; unverifiable claims (not found in evidence, rewrite or remove): " + "; ".join(ungrounded_claims)
        feedback = f"Your previous draft failed quality review. Fix these specific issues and regenerate: {issues}"
        log.warning("a7.regenerating", lead_id=lead_id, touch=touch, attempt=attempt + 1, issues=issues)

    assert draft is not None

    from cold_mailer.contracts.a7_5_qa import QAOutput

    qa = QAOutput(
        lead_id=lead_id, touch=touch, lint_passed=lint_passed(lint_findings), grounded=grounded,
        findings=lint_findings, should_regenerate=False,
    )

    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO drafts
                (lead_id, touch, subject, subject_options, body, linkedin_note, citations,
                 lint_report, lint_passed, grounded, model, prompt_version, regen_count, status)
            VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7::jsonb,$8::jsonb,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (lead_id, touch) DO UPDATE SET
                subject = EXCLUDED.subject, subject_options = EXCLUDED.subject_options,
                body = EXCLUDED.body, linkedin_note = EXCLUDED.linkedin_note,
                citations = EXCLUDED.citations, lint_report = EXCLUDED.lint_report,
                lint_passed = EXCLUDED.lint_passed, grounded = EXCLUDED.grounded,
                model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version,
                regen_count = EXCLUDED.regen_count, status = EXCLUDED.status
            """,
            lead_id, touch, draft.subject_options[0] if draft.subject_options else "",
            json.dumps(draft.subject_options), draft.body, draft.linkedin_note,
            json.dumps([c.model_dump(mode="json") for c in draft.citations]),
            json.dumps([f.model_dump(mode="json") for f in qa.findings]),
            qa.lint_passed, qa.grounded, model, PROMPT_VERSION, regen_count,
            "awaiting_approval" if (qa.lint_passed and qa.grounded) else "draft",
        )
        if touch == 1:
            await conn.execute(
                "UPDATE leads SET status = 'awaiting_approval', updated_at = now() WHERE id = $1", lead_id
            )

    await record_stage_run(
        subject_type="lead", subject_id=lead_id, agent="A7", status="ok",
        confidence=(1.0 if (qa.lint_passed and qa.grounded) else 0.4), model=model,
        tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, latency_ms=latency,
    )

    log.info(
        "a7.generated", domain=domain, lead_id=lead_id, touch=touch,
        lint_passed=qa.lint_passed, grounded=qa.grounded, regen_count=regen_count,
    )
    return GenerationOutput(lead_id=lead_id, touch=touch, draft=draft, prompt_version=PROMPT_VERSION, model=model)


@task_handler("generate_draft")
async def _handle_generate_draft(task: Task) -> None:
    domain = task.payload.get("domain")
    touch = task.payload.get("touch", 1)
    if not domain:
        raise ValueError(f"generate_draft task {task.id} missing 'domain' in payload")
    await generate_draft(domain, task.subject_id, touch)
