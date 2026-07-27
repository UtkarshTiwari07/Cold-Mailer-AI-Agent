"""ATS client parsing, tested against recorded fixtures with the network
call mocked out — deterministic and CI-safe, unlike the live spot-checks
done during development (see DESIGN.md §4, which documents those live
results: Greenhouse/SmartRecruiters/Recruitee/Lever all verified against
real company job boards; Ashby's public API is best-effort/unverified).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cold_mailer.providers.ats.ashby import AshbyClient
from cold_mailer.providers.ats.base import is_engineering
from cold_mailer.providers.ats.lever import LeverClient
from cold_mailer.providers.ats.recruitee import RecruiteeClient
from cold_mailer.providers.ats.smartrecruiters import SmartRecruitersClient


def _fake_client(json_body, status_code=200):
    """Builds a mock that stands in for `httpx.AsyncClient() as client` and
    makes `client.get(...)`/`client.post(...)` return a response whose
    `.json()` is `json_body` and `.status_code` is `status_code`."""
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=json_body)

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_lever_parses_populated_board(lever_fixture):
    with patch("httpx.AsyncClient", return_value=_fake_client(lever_fixture)):
        postings = await LeverClient().fetch_jobs("acme")

    assert len(postings) == 2
    assert postings[0].title == "Senior Backend Engineer"
    assert postings[0].department == "Platform Engineering"
    assert postings[0].location == "Remote - US"
    assert postings[0].posted_at is not None
    assert is_engineering(postings[0]) is True
    assert is_engineering(postings[1]) is False  # "Product Designer"


@pytest.mark.asyncio
async def test_lever_empty_board_returns_empty_list():
    with patch("httpx.AsyncClient", return_value=_fake_client([])):
        postings = await LeverClient().fetch_jobs("no-such-token")
    assert postings == []


@pytest.mark.asyncio
async def test_ashby_filters_unlisted_jobs(ashby_fixture):
    with patch("httpx.AsyncClient", return_value=_fake_client(ashby_fixture)):
        postings = await AshbyClient().fetch_jobs("acme")

    # Fixture has 2 postings; one has isListed=False and must be excluded.
    assert len(postings) == 1
    assert postings[0].title == "Staff Software Engineer"


@pytest.mark.asyncio
async def test_ashby_no_job_board_returns_empty_list():
    with patch("httpx.AsyncClient", return_value=_fake_client({"data": {"jobBoard": None}})):
        postings = await AshbyClient().fetch_jobs("no-such-org")
    assert postings == []


@pytest.mark.asyncio
async def test_smartrecruiters_parses_live_captured_fixture(smartrecruiters_fixture):
    with patch("httpx.AsyncClient", return_value=_fake_client(smartrecruiters_fixture)):
        postings = await SmartRecruitersClient().fetch_jobs("Visa")

    assert len(postings) == len(smartrecruiters_fixture["content"])
    first = postings[0]
    assert first.title == "Sr. Manager"
    assert first.location == "Austin, TX, United States"
    assert first.department == "Software Development/Engineering"
    assert "smartrecruiters.com/Visa" in first.url


@pytest.mark.asyncio
async def test_recruitee_parses_live_captured_fixture(recruitee_fixture):
    with patch("httpx.AsyncClient", return_value=_fake_client(recruitee_fixture)):
        postings = await RecruiteeClient().fetch_jobs("personio")

    assert len(postings) == len(recruitee_fixture["offers"])
    first = postings[0]
    assert "Berlin" in (first.location or "")
    assert first.department == "Produkt"
    assert first.url and "recruitee.com" in first.url


@pytest.mark.asyncio
async def test_ats_client_returns_empty_on_non_200():
    with patch("httpx.AsyncClient", return_value=_fake_client({}, status_code=404)):
        postings = await LeverClient().fetch_jobs("does-not-exist")
    assert postings == []
