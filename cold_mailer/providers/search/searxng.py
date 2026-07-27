"""Self-hosted SearXNG, the primary search provider — free, unlimited,
private. Requires `search: formats: [json]` enabled in the instance's
`settings.yml` (disabled by default for security; see
`deploy/searxng/settings.yml` in this repo, which enables it for this
deployment only)."""

from __future__ import annotations

import httpx

from cold_mailer.core.config import get_settings
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.search.base import SearchProvider, SearchResult


class SearXNGProvider(SearchProvider):
    name = "searxng"

    @network_retry(max_attempts=2)
    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        base_url = get_settings().search.searxng_url
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/search", params={"q": query, "format": "json"}
            )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
            for r in data.get("results", [])[:max_results]
        ]
