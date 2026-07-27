"""The other half of Agent 7.5: verifies every citation a draft makes
actually traces back to gathered evidence, rather than trusting the model's
own claim that it did. This is the guard against the single worst failure
mode in the whole pipeline — a specific, wrong fact about the recruiter's
own company, stated with total confidence.

Matching is deliberately tolerant of paraphrase (exact-substring first,
falling back to word-overlap) since a model asked to "quote the source"
often paraphrases slightly even when told not to — the check should catch
outright fabrication, not penalize a lightly reworded but faithful quote.

Overlap is checked against the UNION of all evidence texts combined, not
document-by-document. A legitimately grounded cold-email sentence often
synthesizes two true facts from two different sources in one clause (a
candidate's own project plus a company fact it responds to) — no single
source document will ever contain most of such a sentence's words, so a
per-document check produces false positives on exactly the compound claims
a good hook is supposed to make. Checking the union is what makes "is every
piece of this traceable to something real" the actual question, rather than
"does one document happen to contain most of this."
"""

from __future__ import annotations

import re

from cold_mailer.contracts.a7_generate import EmailDraft

_WORD_RE = re.compile(r"[a-z0-9]+")

# Filtered out before computing overlap so the ratio reflects shared CONTENT
# words, not shared grammar — otherwise a quote built entirely of common
# words could hit a high ratio against almost any evidence text.
_STOPWORDS = frozenset(
    ["a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with", "that", "this", "these", "those", "is", "are", "was", "were", "be", "been", "being", "as", "at", "by", "from", "into", "onto", "it", "its", "their", "his", "her", "our", "your", "my", "not", "no", "do", "does", "did", "has", "have", "had", "will", "would", "could", "should", "can", "may", "might", "than", "then", "so", "if", "but", "which", "who", "whom", "what", "when", "where", "while", "about", "over", "under", "same", "as", "demands", "strain", "system", "originally", "designed"]
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _content_word_set(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _quote_supported(
    quote: str, evidence_texts: list[str], combined_words: set[str], overlap_threshold: float = 0.55
) -> bool:
    normalized_quote = _normalize(quote)
    if not normalized_quote:
        return False

    for text in evidence_texts:
        if normalized_quote in _normalize(text):
            return True

    quote_words = _content_word_set(quote)
    if len(quote_words) < 3:
        # Too short to meaningfully score by overlap; fall back to requiring
        # the substring check above, which already failed if we're here.
        return False

    overlap = len(quote_words & combined_words) / len(quote_words)
    return overlap >= overlap_threshold


def check_grounding(draft: EmailDraft, evidence_texts: list[str]) -> tuple[bool, list[str]]:
    """Returns (grounded, ungrounded_claims). `grounded` is True only if
    every citation's quote is supported by at least one evidence text. A
    draft with zero citations is treated as grounded by this check alone —
    it has made no verifiable claims to fail on — but the linter and the
    prompt both push A7 toward citing specific claims, so an opener with no
    citations at all is a smell worth a human glancing at, not an
    automatic failure."""
    combined_words: set[str] = set()
    for text in evidence_texts:
        combined_words |= _content_word_set(text)

    ungrounded = [
        c.quote for c in draft.citations
        if not _quote_supported(c.quote, evidence_texts, combined_words)
    ]
    return (len(ungrounded) == 0, ungrounded)
