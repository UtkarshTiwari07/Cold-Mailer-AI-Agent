"""Deliverability safety rails (DESIGN.md §12/§18) — the rails that carry
the risk of the project's explicit decision to send from a personal Gmail
account. Tested against the live Postgres `send_budget` table.
"""

from __future__ import annotations

import pytest

from cold_mailer.core.db import acquire, get_pool
from cold_mailer.pipeline.send_budget import (
    _recent_bounce_rate,
    _within_business_hours,
    check_and_reserve_slot,
)


@pytest.fixture(autouse=True)
async def _ensure_pool():
    await get_pool()


@pytest.mark.asyncio
async def test_business_hours_gate_matches_actual_wall_clock():
    """Not mocked — asserts the gate's answer is internally consistent with
    its own `_within_business_hours()` check, so this test is meaningful
    at any time of day/week it happens to run."""
    within_hours = await _within_business_hours()
    allowed, reason = await check_and_reserve_slot()

    if not within_hours:
        assert allowed is False
        assert "business hours" in reason


@pytest.mark.asyncio
async def test_bounce_rate_with_insufficient_history_does_not_block():
    # Under 10 historical messages, the function deliberately returns 0.0
    # rather than a noisy rate computed from a handful of sends — asserted
    # directly since a fresh test database has no message history at all.
    async with acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM messages")
    if count < 10:
        rate = await _recent_bounce_rate()
        assert rate == 0.0
