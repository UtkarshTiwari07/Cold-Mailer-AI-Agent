"""Serper.dev — paid Google SERP API. Optional last-resort fallback, never
required: if `SERPER_API_KEY` is unset, `search()` returns an empty list
immediately rather than making a doomed request, so the composite chain
falls through cleanly with no special-casing at the call site."""

from __future__ import annotations

import httpx

from cold_mailer.core.config import get_settings
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.search.base import SearchProvider, SearchResult


class SerperProvider(SearchProvider):
    name = "serper"

    @network_retry(max_attempts=2)
    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        api_key = get_settings().search.serper_api_key
        if not api_key:
            return []
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results},
            )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(title=r.get("title", ""), url=r.get("link", ""), snippet=r.get("snippet", ""))
            for r in data.get("organic", [])[:max_results]
        ]
