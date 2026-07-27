"""Generic Redis-backed rate limiting.

Two shapes are enough for this system: a rolling-window counter (search /
crawl / ATS API politeness — "no more than N calls per minute to this host")
and a fixed daily counter (the send budget, which has its own module —
`pipeline/send_budget.py` — because it also carries warm-up-ramp and
circuit-breaker logic specific to deliverability, not just a plain limit).
"""

from __future__ import annotations

import time

from cold_mailer.core.cache import get_redis


class RollingWindowLimiter:
    """Sliding window over a Redis sorted set. One key per (bucket, window)."""

    def __init__(self, bucket: str, max_calls: int, window_s: int) -> None:
        self.key = f"ratelimit:{bucket}"
        self.max_calls = max_calls
        self.window_s = window_s

    async def acquire(self) -> bool:
        """Returns True if the call is allowed (and records it), False if the
        caller should back off."""
        r = get_redis()
        now = time.time()
        window_start = now - self.window_s
        async with r.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(self.key, 0, window_start)
            pipe.zcard(self.key)
            _, count = await pipe.execute()
        if count >= self.max_calls:
            return False
        await r.zadd(self.key, {f"{now}:{id(object())}": now})
        await r.expire(self.key, self.window_s + 5)
        return True

    async def wait_time_s(self) -> float:
        r = get_redis()
        oldest = await r.zrange(self.key, 0, 0, withscores=True)
        if not oldest:
            return 0.0
        _, ts = oldest[0]
        return max(0.0, (ts + self.window_s) - time.time())
