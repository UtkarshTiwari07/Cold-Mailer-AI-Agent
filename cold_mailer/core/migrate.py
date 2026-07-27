"""Minimal, dependency-free migration runner.

Applies every `*.sql` file in `migrations/` in filename order, tracked in a
`schema_migrations` table, skipped if already applied. A file may begin with
a `-- @requires-extension: <name>` comment; if that extension is not listed
in `pg_available_extensions`, the file is skipped with a warning rather than
failing the whole run — this is how `002_pgvector.sql` stays optional on
Postgres instances without pgvector installed.

No down-migrations: at this stage of the project, forward-only is the right
trade-off — simpler, and rollback in practice means restoring a snapshot.
"""

from __future__ import annotations

import asyncio
import re

import asyncpg

from cold_mailer.core.config import get_settings
from cold_mailer.core.logging import configure_logging, get_logger

_REQUIRES_RE = re.compile(r"--\s*@requires-extension:\s*(\S+)")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def run_migrations() -> None:
    settings = get_settings()
    log = get_logger(component="migrate")
    conn = await asyncpg.connect(dsn=settings.db.dsn)
    try:
        await conn.execute(_BOOTSTRAP)
        applied = {r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")}

        for path in sorted(settings.migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue

            sql = path.read_text()
            m = _REQUIRES_RE.search(sql)
            if m:
                ext = m.group(1)
                row = await conn.fetchrow(
                    "SELECT 1 FROM pg_available_extensions WHERE name = $1", ext
                )
                if row is None:
                    log.warning("migration.skipped_missing_extension", file=path.name, extension=ext)
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                    )
                    continue

            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )
            log.info("migration.applied", file=path.name)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    asyncio.run(run_migrations())


if __name__ == "__main__":
    main()
