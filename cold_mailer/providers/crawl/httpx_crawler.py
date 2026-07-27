"""Default crawler: httpx + selectolax. Fast, dependency-light, no browser
binary needed. Sufficient for the large majority of company websites,
careers pages, and engineering blogs, which are still mostly server-
rendered or statically generated. Checks `PageCache` before every fetch —
across ~900-1200 unique company domains, this is the difference between one
real fetch and dozens of them as different agents and pipeline re-runs ask
for the same URL.
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from cold_mailer.core.cache import PageCache
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.crawl.base import Crawler, CrawlResult

log = get_logger(component="crawl.httpx")

_STRIP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer")


def _extract_text(html: str) -> tuple[str | None, str]:
    tree = HTMLParser(html)
    for tag in _STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else None
    body = tree.body
    text = body.text(separator=" ", strip=True) if body else tree.root.text(separator=" ", strip=True)
    return title, " ".join(text.split())


class HttpxCrawler(Crawler):
    def __init__(self, cache: PageCache | None = None) -> None:
        self.cache = cache or PageCache()

    @network_retry(max_attempts=3)
    async def fetch(self, url: str) -> CrawlResult:
        cached = self.cache.get(url)
        if cached is not None:
            title, text = _extract_text(cached)
            return CrawlResult(url=url, status_code=200, title=title, text=text, from_cache=True)

        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; ColdMailerResearchBot/0.1; "
                "+https://github.com/UtkarshTiwari07/Cold-Mailer-AI-Agent)"
            )
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            return CrawlResult(url=url, status_code=resp.status_code, text="")

        self.cache.set(url, resp.text)
        title, text = _extract_text(resp.text)
        return CrawlResult(url=url, status_code=200, title=title, text=text)
