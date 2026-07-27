"""Arq-supervised worker process.

Arq gives us process supervision (graceful shutdown, signal handling, a
standard `arq module.WorkerSettings` CLI entrypoint) but deliberately does
NOT own the work queue — the `tasks` Postgres table does (see
`state_machine.py`). The single cron job below (`tick`) is a polling loop
disguised as a cron job: every `POLL_INTERVAL_S` it drains as many claimable
tasks as fit in one batch, dispatches each to its registered handler
concurrently, and returns. `unique=True` (arq's default) guarantees only one
tick is ever in flight, so there's no risk of two ticks claiming overlapping
batches.

This keeps exactly one source of truth for "what work exists and what state
is it in" — the database — while still getting a real, restart-safe worker
process rather than a hand-rolled `while True: sleep()` loop.
"""

from __future__ import annotations

import asyncio

from arq import cron
from arq.connections import RedisSettings
from arq.cron import CronJob

from cold_mailer.core.config import get_settings
from cold_mailer.core.db import close_pool, get_pool
from cold_mailer.core.logging import configure_logging, get_logger
from cold_mailer.pipeline import stages
from cold_mailer.pipeline.state_machine import (
    claim_tasks,
    complete_task,
    fail_task,
    reap_stale_claims,
)

log = get_logger(component="worker")

POLL_INTERVAL_S = 5
BATCH_SIZE = 10
CLAIM_TIMEOUT_S = 600
_WORKER_ID = "arq-worker"


async def _run_one(task) -> None:
    handler = stages.get_handler(task.kind)
    if handler is None:
        await fail_task(task.id, f"no handler registered for kind={task.kind!r}", retryable=False)
        return
    try:
        await handler(task)
        await complete_task(task.id)
    except Exception as exc:  # noqa: BLE001 - task failures must not crash the tick
        log.error("task.handler_error", task_id=task.id, kind=task.kind, error=str(exc))
        await fail_task(task.id, str(exc))


async def tick(ctx) -> int:
    reaped = await reap_stale_claims(CLAIM_TIMEOUT_S)
    if reaped:
        log.info("worker.reaped_stale", count=reaped)

    processed = 0
    while True:
        tasks = await claim_tasks(_WORKER_ID, batch_size=BATCH_SIZE)
        if not tasks:
            break
        await asyncio.gather(*(_run_one(t) for t in tasks))
        processed += len(tasks)
    return processed


async def _on_startup(ctx) -> None:
    configure_logging(get_settings().obs.log_level, get_settings().obs.log_json)
    stages.load_all_agents()
    await get_pool()
    log.info("worker.started", registered_kinds=stages.registered_kinds())


async def _on_shutdown(ctx) -> None:
    await close_pool()
    log.info("worker.stopped")


class WorkerSettings:
    functions: list = []
    cron_jobs: list[CronJob] = [
        cron(tick, second=set(range(0, 60, POLL_INTERVAL_S)), run_at_startup=True, max_tries=1)
    ]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis.url)
    max_jobs = 1  # the tick body handles its own internal concurrency
