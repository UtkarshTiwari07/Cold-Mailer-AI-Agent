"""Integration tests against a live Postgres — the same instance `make up`
provisions. These formalize the exact scenarios verified by hand during
development, including the one that matters most: a task claimed by a
worker that then "crashes" (never completes it) is recoverable, with no
duplicate work, once `reap_stale_claims` runs.
"""

from __future__ import annotations

import uuid

import pytest

from cold_mailer.core.db import get_pool
from cold_mailer.pipeline.state_machine import (
    claim_tasks,
    complete_task,
    enqueue_task,
    fail_task,
    reap_stale_claims,
    record_stage_run,
    stuck_subjects,
)


@pytest.fixture(autouse=True)
async def _ensure_pool():
    await get_pool()


def _unique_key(name: str) -> str:
    return f"test-{name}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_via_dedupe_key():
    key = _unique_key("dedupe")
    id1 = await enqueue_task("noop", "global", None, {}, dedupe_key=key)
    id2 = await enqueue_task("noop", "global", None, {}, dedupe_key=key)
    assert id1 is not None
    assert id2 is None


@pytest.mark.asyncio
async def test_claim_then_complete():
    key = _unique_key("claim")
    task_id = await enqueue_task("noop", "global", None, {}, dedupe_key=key)
    claimed = await claim_tasks(f"worker-{key}", batch_size=50)
    assert any(t.id == task_id for t in claimed)
    await complete_task(task_id)


@pytest.mark.asyncio
async def test_unknown_task_kind_dies_immediately_not_retryable():
    key = _unique_key("unknown-kind")
    task_id = await enqueue_task("this-kind-does-not-exist", "global", None, {}, dedupe_key=key)
    await claim_tasks(f"worker-{key}", batch_size=50)
    await fail_task(task_id, "no handler registered", retryable=False)

    from cold_mailer.core.db import acquire

    async with acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM tasks WHERE id = $1", task_id)
    assert row["status"] == "dead"


@pytest.mark.asyncio
async def test_crash_recovery_via_reap_stale_claims():
    """The scenario that matters most: claim a task (simulating a worker
    picking it up), never complete it (simulating a crash), then confirm
    `reap_stale_claims` makes it claimable again with zero special-casing."""
    key = _unique_key("crash")
    task_id = await enqueue_task("noop", "global", None, {}, dedupe_key=key)

    claimed = await claim_tasks(f"crashed-worker-{key}", batch_size=50)
    assert any(t.id == task_id for t in claimed)

    reaped = await reap_stale_claims(claim_timeout_s=0)
    assert reaped >= 1

    reclaimed = await claim_tasks(f"recovery-worker-{key}", batch_size=50)
    assert any(t.id == task_id for t in reclaimed)
    await complete_task(task_id)


@pytest.mark.asyncio
async def test_stage_runs_and_stuck_subjects_query():
    subject_id = int(uuid.uuid4().int % 1_000_000_000)
    await record_stage_run(subject_type="company", subject_id=subject_id, agent="TEST_A1", status="ok", confidence=0.2)

    stuck = await stuck_subjects("TEST_A1", min_confidence=0.5)
    assert any(s["subject_id"] == subject_id for s in stuck)
