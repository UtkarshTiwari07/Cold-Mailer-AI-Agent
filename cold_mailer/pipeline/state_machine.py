"""The work queue and its audit trail, both plain Postgres.

Design principle: **the row is the checkpoint.** A `tasks` row's `status`
column is the only source of truth about whether work is pending, in
flight, done, or dead. There is no separate checkpoint file, no in-memory
scheduler state that a crash can desync from disk. Kill the worker process
at any point and:

  * Tasks it already marked `done` stay done — never redone.
  * The task it was mid-processing sits at `claimed` until
    `reap_stale_claims()` notices it has been claimed longer than
    `claim_timeout_s` and puts it back to `pending` — then any worker
    (including a freshly restarted one) picks it up again.
  * Nothing is lost, nothing double-runs, and "what's stuck" is a SQL query
    (`SELECT * FROM tasks WHERE status = 'claimed' AND claimed_at < ...`),
    not an archaeology exercise through logs.

`SELECT ... FOR UPDATE SKIP LOCKED` is what makes concurrent claiming safe:
two workers racing for the same pending row never both get it — one wins,
the other's SKIP LOCKED silently passes over the locked row and claims the
next one instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cold_mailer.core.db import acquire, transaction
from cold_mailer.core.logging import get_logger

log = get_logger(component="state_machine")


@dataclass
class Task:
    id: int
    kind: str
    subject_type: str
    subject_id: int | None
    payload: dict
    attempts: int
    max_attempts: int


async def enqueue_task(
    kind: str,
    subject_type: str,
    subject_id: int | None = None,
    payload: dict | None = None,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> int | None:
    """Idempotent enqueue: if `dedupe_key` collides with an existing task,
    this is a silent no-op (returns None) rather than an error — callers
    that re-enqueue defensively (e.g. "make sure every researched company
    has a fit-synthesis task") don't need their own existence check."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tasks (kind, subject_type, subject_id, payload, dedupe_key, run_after, priority, max_attempts)
            VALUES ($1, $2, $3, $4::jsonb, $5, COALESCE($6, now()), $7, $8)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING id
            """,
            kind, subject_type, subject_id, json.dumps(payload or {}), dedupe_key, run_after, priority, max_attempts,
        )
        return row["id"] if row else None


async def claim_tasks(worker_id: str, batch_size: int = 10) -> list[Task]:
    """Atomically claims up to `batch_size` pending, due tasks. Safe under
    concurrent callers (SKIP LOCKED) and safe to call from multiple worker
    processes at once."""
    async with transaction() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, subject_type, subject_id, payload, attempts, max_attempts
            FROM tasks
            WHERE status = 'pending' AND run_after <= now()
            ORDER BY priority, id
            FOR UPDATE SKIP LOCKED
            LIMIT $1
            """,
            batch_size,
        )
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        await conn.execute(
            "UPDATE tasks SET status = 'claimed', claimed_at = now(), claimed_by = $1, updated_at = now() "
            "WHERE id = ANY($2::bigint[])",
            worker_id, ids,
        )
    return [
        Task(
            id=r["id"], kind=r["kind"], subject_type=r["subject_type"], subject_id=r["subject_id"],
            payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {}),
            attempts=r["attempts"], max_attempts=r["max_attempts"],
        )
        for r in rows
    ]


async def complete_task(task_id: int) -> None:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = now() WHERE id = $1", task_id
        )


async def fail_task(task_id: int, error: str, retryable: bool = True, backoff_s: int = 30) -> None:
    """On failure: retry with backoff if attempts remain and the error is
    retryable, else mark permanently `dead` so it stops being picked up but
    stays queryable for debugging instead of vanishing."""
    async with acquire() as conn:
        row = await conn.fetchrow("SELECT attempts, max_attempts FROM tasks WHERE id = $1", task_id)
        if row is None:
            return
        attempts = row["attempts"] + 1
        if retryable and attempts < row["max_attempts"]:
            run_after = datetime.now(UTC) + timedelta(seconds=backoff_s * attempts)
            await conn.execute(
                "UPDATE tasks SET status = 'pending', attempts = $1, run_after = $2, "
                "last_error = $3, claimed_at = NULL, claimed_by = NULL, updated_at = now() WHERE id = $4",
                attempts, run_after, error, task_id,
            )
        else:
            await conn.execute(
                "UPDATE tasks SET status = 'dead', attempts = $1, last_error = $2, updated_at = now() WHERE id = $3",
                attempts, error, task_id,
            )
    log.warning("task.failed", task_id=task_id, error=error, retryable=retryable)


async def reap_stale_claims(claim_timeout_s: int = 600) -> int:
    """Recovers tasks orphaned by a killed worker: any row still `claimed`
    longer than `claim_timeout_s` goes back to `pending`. Call this on
    worker startup and periodically while running — it is the actual
    mechanism behind "restart the worker and it resumes", not just the
    SKIP LOCKED claim itself, which only prevents double-claiming between
    two *live* workers."""
    async with acquire() as conn:
        rows = await conn.fetch(
            "UPDATE tasks SET status = 'pending', claimed_at = NULL, claimed_by = NULL, updated_at = now() "
            "WHERE status = 'claimed' AND claimed_at < now() - ($1 || ' seconds')::interval "
            "RETURNING id",
            str(claim_timeout_s),
        )
    if rows:
        log.warning("tasks.reaped", count=len(rows), ids=[r["id"] for r in rows])
    return len(rows)


async def record_stage_run(
    *,
    subject_type: str,
    subject_id: int | None,
    agent: str,
    status: str,
    attempt: int = 1,
    confidence: float | None = None,
    input_hash: str | None = None,
    output: dict | None = None,
    error: str | None = None,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
) -> int:
    """The audit ledger. This is what answers "every lead stuck at A3 with
    confidence below 0.5" as a plain query instead of grepping logs, and is
    the raw material Agent 9 aggregates over."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO stage_runs
                (subject_type, subject_id, agent, attempt, status, confidence, input_hash,
                 output, error, model, tokens_in, tokens_out, cost_usd, latency_ms, finished_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,
                    CASE WHEN $5 IN ('ok','error') THEN now() ELSE NULL END)
            RETURNING id
            """,
            subject_type, subject_id, agent, attempt, status, confidence, input_hash,
            json.dumps(output) if output is not None else None, error, model,
            tokens_in, tokens_out, cost_usd, latency_ms,
        )
        return row["id"]


async def stuck_subjects(agent: str, min_confidence: float) -> list[dict[str, Any]]:
    """Example of the "ordinary queryable state" the Postgres-backed design
    buys: every lead/company stuck below a confidence bar at a given agent,
    with no code beyond this query."""
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (subject_id) subject_id, subject_type, confidence, status, finished_at
            FROM stage_runs
            WHERE agent = $1 AND (confidence IS NULL OR confidence < $2)
            ORDER BY subject_id, finished_at DESC NULLS LAST
            """,
            agent, min_confidence,
        )
        return [dict(r) for r in rows]
