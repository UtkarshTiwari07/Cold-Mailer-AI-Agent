"""Crawl4AI-backed crawler — the `full` extra, and DESIGN.md's recommended
production crawler. Renders JavaScript (Playwright under the hood), so it
handles SPA career sites `HttpxCrawler` can't. Lazily imported: a bare
install of this project never pays Playwright's download cost unless this
class is actually instantiated.
"""

from __future__ import annotations

from cold_mailer.core.cache import PageCache
from cold_mailer.providers.crawl.base import Crawler, CrawlResult


class Crawl4AICrawler(Crawler):
    def __init__(self, cache: PageCache | None = None) -> None:
        self.cache = cache or PageCache()
        try:
            from crawl4ai import AsyncWebCrawler  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Crawl4AICrawler requires the 'full' extra: pip install -e '.[full]'"
            ) from exc

    async def fetch(self, url: str) -> CrawlResult:
        cached = self.cache.get(url)
        if cached is not None:
            return CrawlResult(url=url, status_code=200, text=cached, from_cache=True)

        from crawl4ai import AsyncWebCrawler

        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)

        markdown = result.markdown or ""
        if result.success:
            self.cache.set(url, markdown)
        return CrawlResult(
            url=url,
            status_code=200 if result.success else 0,
            title=getattr(result, "title", None),
            text=markdown,
        )
