"""Async Postgres access.

One `asyncpg` pool per process. No ORM: the schema is small and stable enough
that hand-written SQL is clearer than a mapping layer, and it keeps the
"the row is the checkpoint" design honest — nothing hides behind a
lazy-loading relationship.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from cold_mailer.core.config import Settings, get_settings

_pool: asyncpg.Pool | None = None


async def get_pool(settings: Settings | None = None) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        s = settings or get_settings()
        _pool = await asyncpg.create_pool(
            dsn=s.db.dsn, min_size=s.db.pool_min, max_size=s.db.pool_max
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn
