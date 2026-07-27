"""Agent 7.5's deterministic half — rules lifted from the humanizer skill's
`banned-list.md`. These are the same examples verified by hand during
development, formalized as regression tests.
"""

from __future__ import annotations

from cold_mailer.contracts.a7_generate import EmailDraft
from cold_mailer.quality.linter import lint_body, lint_draft, lint_passed, lint_subject_lines

_BAD_PASSAGE = """In today's fast-paced world, it's important to note that we leverage cutting-edge solutions.
Our approach delivers three key benefits. **Speed**: rapid processing. **Reliability**: consistent uptime.
Moreover, this holistic methodology is a true testament to what's possible — truly — when robust engineering
meets innovative thinking."""

_GOOD_PASSAGE = """Saw GitLab Orbit ship last week. That is a hard problem — turning a Rails monolith into something
that can host long running AI agent workloads without falling over. I spent the last year doing exactly
that migration at a smaller scale, cutting queue wait from 42 minutes to 6. Worth a quick chat?"""


def test_bad_passage_fails_on_multiple_rules():
    findings = lint_body(_BAD_PASSAGE)
    rules = {f.rule for f in findings}
    assert "banned_word" in rules
    assert "banned_phrase" in rules
    assert "bold_title_list" in rules
    assert not lint_passed(findings)


def test_good_passage_passes():
    findings = lint_body(_GOOD_PASSAGE)
    assert lint_passed(findings)


def test_em_dash_rule_scales_with_length_not_a_flat_rate():
    # A single dash in a short, cold-email-length body must NOT trip a
    # "per 300 words" rate check literally — see quality/linter.py's
    # em_dash_density comment for why this was a real bug once.
    short_with_one_dash = "Short note, just one dash here. " * 3 + "That is worth a — quick chat."
    findings = lint_body(short_with_one_dash)
    assert not any(f.rule == "em_dash_density" for f in findings)

    two_dashes_short = "One — two — dashes in a short email."
    findings2 = lint_body(two_dashes_short)
    assert any(f.rule == "em_dash_density" for f in findings2)


def test_subject_line_rules():
    findings = lint_subject_lines(["Quick Question!", "reply rates", "a somewhat long subject line here"])
    rules = {f.rule: f.severity for f in findings}
    assert rules["subject_punctuation"] == "error"
    assert rules["subject_case"] == "warning"
    assert rules["subject_length"] == "warning"


def test_subject_count_enforced():
    findings = lint_subject_lines(["only one"])
    assert any(f.rule == "subject_count" and f.severity == "error" for f in findings)


def test_lint_draft_checks_linkedin_note_length():
    # EmailDraft.linkedin_note already carries a Pydantic max_length=300
    # constraint (contracts/a7_generate.py), so a normally-constructed draft
    # can never actually carry an over-length note this far — this test
    # exercises the linter's own check as defense-in-depth, via
    # model_construct() which bypasses validation, the way it would matter
    # if that Pydantic constraint were ever loosened or bypassed upstream.
    draft = EmailDraft.model_construct(
        subject_options=["a b", "c d", "e f"],
        body=_GOOD_PASSAGE,
        linkedin_note="x" * 301,
        citations=[],
    )
    findings = lint_draft(draft)
    assert any(f.rule == "linkedin_note_length" for f in findings)
