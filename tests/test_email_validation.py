"""Agent 0 — dedupe and deny-list logic (pure, no network) plus a live DNS
check exercising exactly what was verified by hand during development:
gmail.com resolves, a nonsense domain raises NXDOMAIN. This project's own
use case assumes real network access (it is, after all, a web-research
pipeline), so this is not skipped/mocked — it is the honest behavior A0
depends on in production.
"""

from __future__ import annotations

import pytest

from cold_mailer.agents.a0_ingest import _resolve_mx, dedupe, validate_row
from cold_mailer.contracts.a0_ingest import RawLeadRow, Validity
from cold_mailer.quality.deny_lists import DISPOSABLE_DOMAINS, ROLE_ACCOUNT_LOCAL_PARTS


def test_dedupe_keeps_first_occurrence_case_insensitive():
    rows = [
        RawLeadRow(raw_email="Jane@Acme.com", source_row=2),
        RawLeadRow(raw_email="jane@acme.com", source_row=7),
        RawLeadRow(raw_email="other@acme.com", source_row=3),
    ]
    unique = dedupe(rows)
    assert len(unique) == 2
    assert unique[0].source_row == 2


def test_disposable_and_role_deny_lists_are_nonempty_and_lowercase():
    assert len(DISPOSABLE_DOMAINS) > 10
    assert all(d == d.lower() for d in DISPOSABLE_DOMAINS)
    assert "hr" in ROLE_ACCOUNT_LOCAL_PARTS
    assert "careers" in ROLE_ACCOUNT_LOCAL_PARTS


@pytest.mark.asyncio
async def test_bad_syntax_is_invalid_without_any_network_call():
    row = RawLeadRow(raw_email="not-an-email-at-all", source_row=1)
    result = await validate_row(row)
    assert result.validity == Validity.invalid
    assert result.detail.syntax_ok is False


@pytest.mark.asyncio
async def test_disposable_domain_is_invalid(monkeypatch):
    # MX lookup is stubbed so this test doesn't depend on the disposable
    # domain's actual DNS state — only the deny-list branch is under test.
    async def fake_resolve(domain):
        return True, "mx_found"

    monkeypatch.setattr("cold_mailer.agents.a0_ingest._resolve_mx", fake_resolve)
    row = RawLeadRow(raw_email="temp@mailinator.com", source_row=1)
    result = await validate_row(row)
    assert result.validity == Validity.invalid
    assert result.detail.is_disposable is True


@pytest.mark.asyncio
async def test_role_account_is_risky_not_invalid(monkeypatch):
    async def fake_resolve(domain):
        return True, "mx_found"

    monkeypatch.setattr("cold_mailer.agents.a0_ingest._resolve_mx", fake_resolve)
    row = RawLeadRow(raw_email="hr@some-real-looking-company.com", source_row=1)
    result = await validate_row(row)
    assert result.validity == Validity.risky
    assert result.detail.is_role_account is True


@pytest.mark.asyncio
async def test_live_dns_resolves_gmail_and_rejects_nonsense_domain():
    has_mx, reason = await _resolve_mx("gmail.com")
    assert has_mx is True

    has_mx2, reason2 = await _resolve_mx("this-domain-should-not-exist-abc123xyz.com")
    assert has_mx2 is False
    assert reason2 == "nxdomain"
