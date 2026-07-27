"""Shared helpers for the company-research agents (A1/A2/A4).

Evidence is gathered once, by A1, and stored in Postgres keyed by
`company_id` — A2 and A4 read it back rather than re-crawling, which is
where the "research runs once per company, not once per lead" saving
actually gets realized in code (a company with 3 leads pays for one crawl +
three cheap reads, not three crawls).
"""

from __future__ import annotations

import hashlib

from cold_mailer.core.db import acquire
from cold_mailer.core.logging import get_logger
from cold_mailer.providers.crawl.httpx_crawler import HttpxCrawler
from cold_mailer.providers.search.base import CompositeSearchProvider
from cold_mailer.providers.search.ddgs import DDGSProvider
from cold_mailer.providers.search.searxng import SearXNGProvider
from cold_mailer.providers.search.serper import SerperProvider

log = get_logger(component="research_common")

_crawler = HttpxCrawler()
_search = CompositeSearchProvider([SearXNGProvider(), DDGSProvider(), SerperProvider()])

MAX_EVIDENCE_TEXT = 4000  # chars stored per page — enough for a model to work with, not the whole site


async def get_or_create_company(domain: str) -> int:
    async with acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO companies (domain) VALUES ($1) "
            "ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain "
            "RETURNING id",
            domain,
        )
        return row["id"]


async def store_evidence(company_id: int, url: str | None, kind: str, title: str | None, text: str) -> int | None:
    if not text.strip():
        return None
    text = text[:MAX_EVIDENCE_TEXT]
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    async with acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO evidence (company_id, url, kind, title, text, content_hash) "
            "VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (company_id, content_hash) DO NOTHING "
            "RETURNING id",
            company_id, url, kind, title, text, content_hash,
        )
        if row:
            return row["id"]
        existing = await conn.fetchrow(
            "SELECT id FROM evidence WHERE company_id = $1 AND content_hash = $2", company_id, content_hash
        )
        return existing["id"] if existing else None


async def get_evidence(company_id: int, limit: int = 30) -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, url, kind, title, text FROM evidence WHERE company_id = $1 "
            "ORDER BY fetched_at DESC LIMIT $2",
            company_id, limit,
        )
        return [dict(r) for r in rows]


def format_evidence_for_prompt(evidence: list[dict], max_chars: int = 6000) -> str:
    """Numbered blocks a model can cite back by id: `[E<id>] <url>\ntext...`.
    Truncated to `max_chars` total — evidence volume grows with how many
    pages A1 crawled, and this is the backstop against an unbounded prompt
    (and unbounded cost) regardless of how much was gathered upstream."""
    parts: list[str] = []
    budget = max_chars
    for e in evidence:
        block = f"[E{e['id']}] {e.get('url') or e.get('kind')}\n{e['text']}\n"
        if budget - len(block) < 0:
            block = block[: max(budget, 0)]
        parts.append(block)
        budget -= len(block)
        if budget <= 0:
            break
    return "\n".join(parts)


async def crawl_and_store(company_id: int, url: str, kind: str = "crawl") -> dict | None:
    try:
        result = await _crawler.fetch(url)
    except Exception as exc:  # noqa: BLE001 - one bad URL must not sink the whole research pass
        log.warning("research.crawl_failed", url=url, error=str(exc))
        return None
    if result.status_code != 200 or not result.text:
        return None
    evidence_id = await store_evidence(company_id, url, kind, result.title, result.text)
    if evidence_id is None:
        return None
    return {"id": evidence_id, "url": url, "title": result.title, "text": result.text[:MAX_EVIDENCE_TEXT]}


async def search_and_store(company_id: int, query: str, max_results: int = 5) -> list[dict]:
    results = await _search.search(query, max_results=max_results)
    stored = []
    for r in results:
        evidence_id = await store_evidence(company_id, r.url, "search_snippet", r.title, r.snippet)
        if evidence_id is not None:
            stored.append({"id": evidence_id, "url": r.url, "title": r.title, "text": r.snippet})
    return stored
