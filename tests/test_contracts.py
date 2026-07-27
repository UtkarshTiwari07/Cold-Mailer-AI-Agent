"""Round-trip serialization for the Pydantic I/O contracts — the
replaceability boundary every agent is built against (DESIGN.md §2). If a
contract can't survive `model_dump_json` -> `model_validate_json`, nothing
built on top of it (the LLM cache, the Postgres JSONB columns, the approval
queue) can trust it either.
"""

from __future__ import annotations

from cold_mailer.contracts.a1_discovery import DiscoveryOutput
from cold_mailer.contracts.a4_classify import ClassificationOutput, CompanyCategory, RelevanceTier
from cold_mailer.contracts.a5_fit import FitOutput, Hook
from cold_mailer.contracts.a7_generate import EmailDraft
from cold_mailer.contracts.a10_triage import ReplyKind, TriageOutput
from cold_mailer.contracts.common import CompanySize, Confidence


def _round_trip(model):
    cls = type(model)
    return cls.model_validate_json(model.model_dump_json())


def test_discovery_output_round_trip():
    original = DiscoveryOutput(
        domain="example.com", name="Example", company_size=CompanySize.medium,
        confidence=Confidence.high, evidence_ids=[1, 2, 3],
    )
    restored = _round_trip(original)
    assert restored == original


def test_classification_output_round_trip_and_score_bounds():
    original = ClassificationOutput(
        domain="example.com", categories=[CompanyCategory.ai_startup, CompanyCategory.series_b],
        relevance_score=72, relevance_tier=RelevanceTier.high, rationale="test",
    )
    restored = _round_trip(original)
    assert restored == original
    assert 0 <= restored.relevance_score <= 100


def test_fit_output_with_hooks_round_trip():
    original = FitOutput(
        domain="example.com", lead_id=1,
        hooks=[Hook(text="did X", strength=5, supporting_project="proj")],
        strongest_angle="the angle",
    )
    restored = _round_trip(original)
    assert restored.hooks[0].strength == 5


def test_email_draft_requires_exactly_three_subjects():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EmailDraft(subject_options=["only one"], body="hi")

    draft = EmailDraft(subject_options=["a b", "c d", "e f"], body="hi")
    assert len(draft.subject_options) == 3


def test_triage_output_round_trip():
    original = TriageOutput(
        lead_id=1, kind=ReplyKind.bounce_hard, should_stop_sequence=True, should_suppress=True,
    )
    restored = _round_trip(original)
    assert restored == original
