"""Agent 0 — Ingestion & Validation (not in the original brief, added because
the source is an ~1800-row spreadsheet collected over time and is known to
contain stale, agency, and role-account addresses).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RawLeadRow(BaseModel):
    """One row from the source Excel/CSV, before any cleaning."""

    raw_email: str
    display_name: str | None = None
    source_row: int
    extra: dict[str, str] = Field(default_factory=dict)


class Validity(str, Enum):
    valid = "valid"
    risky = "risky"  # e.g. catch-all domain — plausible but unconfirmable
    invalid = "invalid"


class ValidationDetail(BaseModel):
    syntax_ok: bool
    has_mx: bool
    is_disposable: bool
    is_role_account: bool  # info@, hr@, careers@, recruiting@ ...
    is_catch_all: bool | None = None  # None = not checked
    reason: str


class IngestOutput(BaseModel):
    email: str
    domain: str
    display_name: str | None = None
    validity: Validity
    detail: ValidationDetail
    duplicate_of_row: int | None = None
