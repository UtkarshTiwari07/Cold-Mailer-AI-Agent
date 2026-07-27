"""Covers a real gap found during a live end-to-end run: a citation naming
a project's tech stack ("Go, Redis, Kubernetes") was wrongly flagged
ungrounded because `_profile_grounding_texts()` only pulled project
description/outcome/achievement text, never each project's `technologies`
list or the profile's top-level `skills` — both are genuine, first-party
facts about the candidate and should ground a citation just as much as a
project's prose description does.
"""

from __future__ import annotations

from pathlib import Path

from cold_mailer.agents.a6_profile import load_profile
from cold_mailer.agents.a7_generate import _profile_grounding_texts
from cold_mailer.contracts.a7_generate import EmailDraft
from cold_mailer.contracts.common import Citation
from cold_mailer.quality.grounding import check_grounding

_FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "test_candidate_profile.yaml"


def test_profile_grounding_texts_include_project_technologies_and_skills(monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", str(_FIXTURE_PROFILE))
    load_profile.cache_clear()
    try:
        texts = _profile_grounding_texts()
        combined = " ".join(texts).lower()
        # From tests/fixtures/test_candidate_profile.yaml's project technologies.
        assert "kubernetes" in combined
        assert "redis" in combined
        # From the profile's top-level skills list.
        assert "postgresql" in combined
    finally:
        load_profile.cache_clear()


def test_citation_naming_a_real_tech_stack_is_grounded(monkeypatch):
    # "Go, Redis, Kubernetes" is the exact citation text observed in the live
    # run that surfaced this gap — a short, direct list pulled straight from
    # a project's `technologies`, not a full descriptive sentence.
    monkeypatch.setenv("PROFILE_PATH", str(_FIXTURE_PROFILE))
    load_profile.cache_clear()
    try:
        evidence = _profile_grounding_texts()
        draft = EmailDraft(
            subject_options=["a b", "c d", "e f"], body="body",
            citations=[Citation(quote="Go, Redis, Kubernetes")],
        )
        grounded, ungrounded = check_grounding(draft, evidence)
        assert grounded is True, f"expected grounded, got ungrounded={ungrounded}"
    finally:
        load_profile.cache_clear()
