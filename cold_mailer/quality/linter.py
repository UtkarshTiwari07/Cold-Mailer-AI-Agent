"""Agent 7.5's deterministic half: every rule here comes straight from
`~/.claude/skills/humanizer/references/banned-list.md`, turned into regex
and sentence-statistics checks. Zero LLM cost on the common path — this
runs on every draft before any model ever judges it, and most AI-tell
patterns are exactly this mechanical (banned vocabulary, uniform sentence
length, bullet-title lists) so a free deterministic pass catches the bulk
of them before an expensive one would need to.
"""

from __future__ import annotations

import re

from cold_mailer.contracts.a7_5_qa import LintFinding
from cold_mailer.contracts.a7_generate import EmailDraft

_BANNED_WORDS = [
    "delve", "unlock", "leverage", "harness", "robust", "seamless", "elevate",
    "showcase", "vibrant", "tapestry", "holistic", "cutting-edge", "game-changer",
    "unparalleled", "transformative", "revolutionize", "synergy", "best-in-class",
    "leading provider", "circle back",
]

_BANNED_PHRASES = [
    "in today's fast-paced", "it's important to note that", "it's worth noting",
    "at the end of the day", "dive into", "let's dive in", "deep dive",
    "unlock the potential", "unlock the full potential", "navigate the complexities of",
    "plays a crucial role", "plays a pivotal role", "plays a vital role",
    "a testament to", "when it comes to", "in the ever-evolving world of",
    "not only", "i hope this email finds you well", "i came across your profile",
    "i'm excited to apply", "just checking in",
]

_HEDGE_PHRASES = [
    "it could be argued", "some might say", "there are several factors at play",
    "this may vary depending on", "arguably", "generally speaking", "in many cases",
]

_TRANSITION_WORDS = ["moreover", "furthermore", "additionally", "in conclusion"]

_BOLD_TITLE_LIST_RE = re.compile(r"\*\*[^*]{1,40}\*\*\s*:")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _sentences(text: str) -> list[str]:
    # Good enough for cold-email-length prose; not a full sentence tokenizer.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _find_any(haystack_lower: str, needles: list[str]) -> list[str]:
    return [n for n in needles if n in haystack_lower]


def lint_body(body: str) -> list[LintFinding]:
    findings: list[LintFinding] = []
    lower = body.lower()
    words = _word_count(body)

    for phrase in _find_any(lower, _BANNED_WORDS):
        findings.append(LintFinding(rule="banned_word", severity="error", detail=f"Contains banned word/phrase: {phrase!r}"))

    for phrase in _find_any(lower, _BANNED_PHRASES):
        findings.append(LintFinding(rule="banned_phrase", severity="error", detail=f"Contains banned AI-tell phrase: {phrase!r}"))

    for phrase in _find_any(lower, _HEDGE_PHRASES):
        findings.append(LintFinding(rule="hedge_phrase", severity="warning", detail=f"Reflexive hedge: {phrase!r}"))

    transition_hits = [w for w in _TRANSITION_WORDS if lower.count(w) > 0]
    if transition_hits:
        findings.append(LintFinding(
            rule="transition_stacking", severity="warning",
            detail=f"Stacked transition word(s): {transition_hits}",
        ))

    # "Max ~1 per 300 words" is a rate meant for longer-form writing. A cold
    # email is ~100-200 words, where a literal reading of that rate would
    # flag even a single, well-placed dash — so the floor is 1 dash allowed
    # regardless of length, and the rate only bites once the body is long
    # enough for a second one to be earned.
    em_dash_count = body.count("—")
    max_allowed = max(1, round(words / 300))
    if em_dash_count > max_allowed:
        findings.append(LintFinding(
            rule="em_dash_density", severity="error",
            detail=f"{em_dash_count} em dash(es) in {words} words — max {max_allowed} for this length",
        ))

    if body.count(";") >= 2:
        findings.append(LintFinding(
            rule="semicolon_overuse", severity="warning",
            detail=f"{body.count(';')} semicolons — near-zero in real professional writing",
        ))

    if _BOLD_TITLE_LIST_RE.search(body):
        findings.append(LintFinding(
            rule="bold_title_list", severity="error",
            detail="Bold-title-plus-fragment list structure ('**Title**: ...') — rewrite as prose",
        ))

    sentences = _sentences(body)
    if sentences:
        lengths = [_word_count(s) for s in sentences]
        has_short = any(length < 6 for length in lengths if length > 0)
        if words >= 60 and not has_short:
            findings.append(LintFinding(
                rule="burstiness_missing_short_sentence", severity="warning",
                detail="No sentence under 6 words — uniform sentence length reads as AI-written",
            ))
        for i in range(len(lengths) - 2):
            a, b, c = lengths[i], lengths[i + 1], lengths[i + 2]
            if max(a, b, c) - min(a, b, c) <= 2 and min(a, b, c) > 0:
                findings.append(LintFinding(
                    rule="burstiness_uniform_run", severity="warning",
                    detail=f"Three consecutive sentences within 2 words of each other in length ({a},{b},{c})",
                    span=sentences[i][:60],
                ))
                break  # one flag is enough signal; don't spam identical findings

    return findings


def lint_subject_lines(subject_options: list[str]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    if len(subject_options) != 3:
        findings.append(LintFinding(
            rule="subject_count", severity="error",
            detail=f"Expected exactly 3 subject options, got {len(subject_options)}",
        ))
    for subj in subject_options:
        word_count = len(subj.split())
        if not (2 <= word_count <= 4):
            findings.append(LintFinding(
                rule="subject_length", severity="warning",
                detail=f"Subject {subj!r} has {word_count} words, expected 2-4", span=subj,
            ))
        if subj != subj.lower():
            findings.append(LintFinding(
                rule="subject_case", severity="warning", detail=f"Subject not lowercase: {subj!r}", span=subj,
            ))
        if any(ch in subj for ch in "!?"):
            findings.append(LintFinding(
                rule="subject_punctuation", severity="error",
                detail=f"Subject uses urgency punctuation: {subj!r}", span=subj,
            ))
    return findings


def lint_draft(draft: EmailDraft) -> list[LintFinding]:
    findings = lint_body(draft.body) + lint_subject_lines(draft.subject_options)
    if draft.linkedin_note and len(draft.linkedin_note) > 300:
        findings.append(LintFinding(
            rule="linkedin_note_length", severity="error",
            detail=f"LinkedIn note is {len(draft.linkedin_note)} chars, max 300",
        ))
    return findings


def lint_passed(findings: list[LintFinding]) -> bool:
    return not any(f.severity == "error" for f in findings)
