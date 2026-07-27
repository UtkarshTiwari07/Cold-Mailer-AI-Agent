"""Two caches, two different jobs:

`RedisCache`   — hot, ephemeral: LLM-response fast path, rate-limit counters,
                 the running spend total. Fine to lose on a Redis restart.
`PageCache`    — content-addressed crawl cache on disk, keyed by URL. Slower
                 but survives restarts; re-fetching the same careers page
                 across pipeline runs is the single most wasteful thing a
                 naive crawler does, so this is checked before every fetch.

The persistent LLM-response cache (Postgres `llm_cache`, keyed on
`(prompt_hash, model, schema_version)`) lives in `core/llm.py` next to the
code that computes the hash, since the key derivation and the cache are one
concept there.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from redis.asyncio import Redis

from cold_mailer.core.config import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis.url, decode_responses=True)
    return _redis


class RedisCache:
    def __init__(self) -> None:
        self.r = get_redis()

    async def get_json(self, key: str) -> dict | None:
        raw = await self.r.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: dict, ttl_s: int | None = None) -> None:
        await self.r.set(key, json.dumps(value), ex=ttl_s)

    async def incrbyfloat(self, key: str, amount: float) -> float:
        return float(await self.r.incrbyfloat(key, amount))

    async def incr(self, key: str, amount: int = 1, ttl_s: int | None = None) -> int:
        val = await self.r.incrby(key, amount)
        if ttl_s and val == amount:
            await self.r.expire(key, ttl_s)
        return int(val)


class PageCache:
    """Disk-backed, content-addressed by URL. One JSON file per URL under
    `var/pagecache/`. Not shared across machines — fine for a single-operator
    pipeline; the migration note for a multi-worker deployment is to swap
    this for the same interface backed by S3/MinIO, which DESIGN.md covers.
    """

    def __init__(self, root: Path | None = None, ttl_days: int = 14) -> None:
        self.root = root or (Path.cwd() / "var" / "pagecache")
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_s = ttl_days * 86400

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, url: str) -> str | None:
        path = self._path(url)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if time.time() - data["fetched_at"] > self.ttl_s:
            return None
        return data["text"]

    def set(self, url: str, text: str) -> None:
        path = self._path(url)
        path.write_text(json.dumps({"url": url, "fetched_at": time.time(), "text": text}))

    def content_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()
