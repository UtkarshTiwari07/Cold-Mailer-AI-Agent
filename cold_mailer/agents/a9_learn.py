"""Agent 9 — Learning.

Pure SQL aggregation, no LLM call: reply-rate math is arithmetic, not
judgment, and this needs to run often and cheaply — after every send, every
reply — without adding to the token budget. `recommendations` are simple,
legible rule-based observations over the aggregated numbers on purpose:
"subject line X has 12 sends and a 40% reply rate" is a more useful answer
to act on than an LLM's paraphrase of the same fact.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cold_mailer.contracts.a9_learn import LearningReport, VariantStats
from cold_mailer.core.db import acquire
from cold_mailer.core.logging import get_logger

log = get_logger(component="a9_learn")

_MIN_SAMPLE_FOR_RECOMMENDATION = 3


async def _variant_stats(group_col_sql: str, join_sql: str = "") -> list[VariantStats]:
    query = f"""
        SELECT {group_col_sql} AS key,
               count(*) AS sent,
               count(*) FILTER (WHERE m.status = 'replied') AS replied,
               count(*) FILTER (WHERE m.status = 'bounced') AS bounced
        FROM messages m
        {join_sql}
        GROUP BY key
        ORDER BY count(*) DESC
    """
    async with acquire() as conn:
        rows = await conn.fetch(query)
    stats = []
    for r in rows:
        sent = r["sent"] or 0
        replied = r["replied"] or 0
        bounced = r["bounced"] or 0
        stats.append(
            VariantStats(
                key=str(r["key"]), sent=sent, replied=replied, bounced=bounced,
                reply_rate=(replied / sent if sent else 0.0),
                bounce_rate=(bounced / sent if sent else 0.0),
            )
        )
    return stats


async def generate_report() -> LearningReport:
    async with acquire() as conn:
        totals = await conn.fetchrow(
            "SELECT count(*) AS sent, count(*) FILTER (WHERE status = 'replied') AS replied, "
            "count(*) FILTER (WHERE status = 'bounced') AS bounced FROM messages"
        )

    by_subject = await _variant_stats("m.subject")
    by_prompt_version = await _variant_stats("d.prompt_version", "JOIN drafts d ON d.id = m.draft_id")
    # Grouped on the raw JSON-array text rather than exploded per category —
    # a company with two labels gets its own bucket rather than counting
    # toward both. Simple and correct for "which exact combination performs
    # best"; exploding via jsonb_array_elements_text is the upgrade if
    # per-label attribution (not per-combination) is what's needed later.
    by_category = await _variant_stats(
        "COALESCE(c.classification->>'categories', 'unknown')",
        "JOIN leads l ON l.id = m.lead_id LEFT JOIN companies c ON c.id = l.company_id",
    )
    by_tier = await _variant_stats(
        "COALESCE(f.tier, 'unknown')",
        "JOIN leads l ON l.id = m.lead_id LEFT JOIN fit_analyses f ON f.lead_id = l.id",
    )

    sent = totals["sent"] or 0
    replied = totals["replied"] or 0
    bounced = totals["bounced"] or 0

    recommendations: list[str] = []
    eligible = [s for s in by_subject if s.sent >= _MIN_SAMPLE_FOR_RECOMMENDATION]
    if eligible:
        best = max(eligible, key=lambda s: s.reply_rate)
        recommendations.append(
            f"Subject {best.key!r} has the highest reply rate so far "
            f"({best.reply_rate:.0%} over {best.sent} sends) — weight new drafts toward its style."
        )
    for s in by_category:
        if s.sent >= _MIN_SAMPLE_FOR_RECOMMENDATION and s.bounce_rate > 0.05:
            recommendations.append(
                f"Category {s.key} shows an elevated bounce rate ({s.bounce_rate:.0%} over {s.sent} "
                f"sends) — check email-validation quality for that segment."
            )
    if not recommendations:
        recommendations.append(
            f"Only {sent} send(s) recorded so far — not enough volume yet for a confident recommendation."
        )

    return LearningReport(
        generated_at=datetime.now(UTC).isoformat(),
        overall_reply_rate=(replied / sent if sent else 0.0),
        overall_bounce_rate=(bounced / sent if sent else 0.0),
        by_subject_line=by_subject, by_prompt_version=by_prompt_version,
        by_category=by_category, by_relevance_tier=by_tier,
        recommendations=recommendations,
    )
