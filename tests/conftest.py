"""Shared test fixtures.

DB/Redis-dependent tests assume `make up` has been run (or the equivalent
natively-installed services are reachable at the DSN/URL in `.env`/env
vars) — consistent with how every live verification in this project was
actually done. Pure-logic tests (linter, grounding, ATS fixture parsing,
contract round-trips) need neither and always run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def lever_fixture() -> list[dict]:
    return json.loads((FIXTURES_DIR / "lever_sample.json").read_text())


@pytest.fixture
def ashby_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "ashby_sample.json").read_text())


@pytest.fixture
def smartrecruiters_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "smartrecruiters_visa.json").read_text())


@pytest.fixture
def recruitee_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "recruitee_personio.json").read_text())
