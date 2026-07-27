"""Agent 7.5 — QA Guardrail (added: the brief has no gate between "generate"
and "send", which is the single highest-risk gap in the whole pipeline — a
wrong fact about the recruiter's own company is worse than no email at all).
Deterministic and free: no LLM call on the common path.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LintFinding(BaseModel):
    rule: str
    severity: str = Field(description="'error' | 'warning'")
    detail: str
    span: str | None = None


class QAInput(BaseModel):
    lead_id: int
    touch: int
    draft: dict  # EmailDraft.model_dump()
    evidence_texts: list[str] = Field(default_factory=list)


class QAOutput(BaseModel):
    lead_id: int
    touch: int
    lint_passed: bool
    grounded: bool
    findings: list[LintFinding] = Field(default_factory=list)
    ungrounded_claims: list[str] = Field(default_factory=list)
    should_regenerate: bool
