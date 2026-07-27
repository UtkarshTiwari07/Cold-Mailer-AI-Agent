"""Search provider abstraction with a fallback chain and a per-provider
circuit breaker.

SearXNG (self-hosted, unlimited, free) is the primary. It aggregates 70+
backends and has no per-query cost, but a self-hosted instance can hit
upstream rate limits and go quiet at odd hours — exactly the failure mode a
circuit breaker exists for: after a few consecutive failures, stop hammering
it for a cooldown window and fall through to DDGS (also free, no API key,
somewhat rate-limited itself) and then Serper (paid, only used if
`SERPER_API_KEY` is set — never required).

The breaker is in-process, not Redis-shared, which is the right trade-off
for a single-operator pipeline with one or a handful of worker processes;
DESIGN.md notes the Redis-shared version as the multi-worker upgrade.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from pydantic import BaseModel

from cold_mailer.core.logging import get_logger

log = get_logger(component="search")


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        ...


class _CircuitBreaker:
    def __init__(self, fail_threshold: int = 3, cooldown_s: int = 120) -> None:
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self._consecutive_failures = 0
        self._open_until = 0.0

    def is_open(self) -> bool:
        return time.monotonic() < self._open_until

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.fail_threshold:
            self._open_until = time.monotonic() + self.cooldown_s


class CompositeSearchProvider(SearchProvider):
    """Tries each provider in order; skips any whose breaker is open;
    returns the first non-empty result set."""

    name = "composite"

    def __init__(self, providers: list[SearchProvider]) -> None:
        self.providers = providers
        self._breakers = {p.name: _CircuitBreaker() for p in providers}

    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        for provider in self.providers:
            breaker = self._breakers[provider.name]
            if breaker.is_open():
                log.info("search.breaker_open_skip", provider=provider.name)
                continue
            try:
                results = await provider.search(query, max_results)
                breaker.record_success()
                if results:
                    return results
            except Exception as exc:  # noqa: BLE001 - any provider failure falls through
                breaker.record_failure()
                log.warning("search.provider_failed", provider=provider.name, error=str(exc))
        return []
