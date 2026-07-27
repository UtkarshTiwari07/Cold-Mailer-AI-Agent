"""DuckDuckGo search via the `ddgs` package — free, no API key, second in
the fallback chain behind SearXNG.

Observed live from this deployment's sandbox: the default backend routed
through Yahoo and timed out; forcing `backend="duckduckgo"` avoided the
timeout but returned zero results (likely the egress IP getting a blocked/
empty response, a known failure mode for scraped search backends from
datacenter IPs). That is exactly the behavior the composite provider's
circuit breaker is built to route around — this provider is expected to be
unreliable some of the time, by design gets skipped on repeated failure,
and is never the only path. `ddgs.DDGS` is a synchronous client, so calls
are pushed to a thread via `asyncio.to_thread` to avoid blocking the event
loop.
"""

from __future__ import annotations

import asyncio

from cold_mailer.providers.search.base import SearchProvider, SearchResult


class DDGSProvider(SearchProvider):
    name = "ddgs"

    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query, max_results)

    @staticmethod
    def _search_sync(query: str, max_results: int) -> list[SearchResult]:
        from ddgs import DDGS

        with DDGS(timeout=15) as ddgs:
            raw = ddgs.text(query, max_results=max_results, backend="duckduckgo")
        return [
            SearchResult(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
            for r in raw
        ]
