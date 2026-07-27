"""Crawler abstraction. `HttpxCrawler` (default, no extra install) handles
static and server-rendered pages, which covers most careers/engineering-blog
pages. `Crawl4AICrawler` (the `full` extra) adds a real browser for
JS-heavy single-page-app career sites and is the recommended production
upgrade — swapped in behind the same interface, no caller changes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class CrawlResult(BaseModel):
    url: str
    status_code: int
    title: str | None = None
    text: str = ""
    from_cache: bool = False


class Crawler(ABC):
    @abstractmethod
    async def fetch(self, url: str) -> CrawlResult:
        ...
