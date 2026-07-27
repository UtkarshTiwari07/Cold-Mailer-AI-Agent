"""Agent 7.5's other half: citation-vs-evidence verification. Covers the
real bug hit and fixed during development — a citation that legitimately
blends a candidate fact with a company fact must be checkable against the
UNION of evidence, not any single document (see quality/grounding.py's
module docstring and DESIGN.md §9's A7 row for the full story).
"""

from __future__ import annotations

from cold_mailer.contracts.a7_generate import EmailDraft
from cold_mailer.contracts.common import Citation
from cold_mailer.quality.grounding import check_grounding


def _draft_with_citations(quotes: list[str]) -> EmailDraft:
    return EmailDraft(
        subject_options=["a b", "c d", "e f"],
        body="body text",
        citations=[Citation(quote=q) for q in quotes],
    )


def test_exact_substring_is_grounded():
    evidence = ["GitLab shipped 60+ improvements across AI, Security and more in release 19.2."]
    draft = _draft_with_citations(["shipped 60+ improvements"])
    grounded, ungrounded = check_grounding(draft, evidence)
    assert grounded is True
    assert ungrounded == []


def test_fabricated_claim_is_not_grounded():
    evidence = ["GitLab is a DevSecOps platform used by Ticketmaster and Jaguar Land Rover."]
    draft = _draft_with_citations(["GitLab raised a $500M round from Sequoia last month"])
    grounded, ungrounded = check_grounding(draft, evidence)
    assert grounded is False
    assert len(ungrounded) == 1


def test_compound_claim_grounded_across_two_separate_sources():
    """The exact scenario that was a real bug: a citation spanning a
    candidate fact (from the profile) and a company fact (from evidence) in
    one string must be checkable against the union of both sources."""
    company_evidence = "GitLab's Duo Agent Platform pushes long-running AI inference into a Rails monolith."
    profile_evidence = "Extracted a billing module out of a Rails monolith into an independently deployable service."
    evidence = [company_evidence, profile_evidence]

    compound_quote = (
        "extracted a billing module from a Rails monolith into an independent service, "
        "the same pattern GitLab's Duo Agent Platform demands for long-running AI inference"
    )
    draft = _draft_with_citations([compound_quote])
    grounded, ungrounded = check_grounding(draft, evidence)
    assert grounded is True, f"expected grounded, got ungrounded={ungrounded}"


def test_no_citations_is_treated_as_grounded():
    draft = _draft_with_citations([])
    grounded, ungrounded = check_grounding(draft, ["some evidence"])
    assert grounded is True
    assert ungrounded == []
