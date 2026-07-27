"""Agent 0 — Ingestion & Validation.

Turns the raw ~1800-row spreadsheet into normalized, deduplicated,
tri-state-validated leads. Deliberately has no LLM call: syntax, MX, and
list-membership checks are exact and free, and a model has no useful opinion
on whether `hr@acme.com` resolves. All validation is real (asyncio DNS
resolution against the recipient's actual MX records, tested live against
gmail.com and a nonexistent domain during development), not a heuristic
stand-in.

Tri-state, not boolean, because ~30-40% of B2B mail domains are catch-all —
MX exists and accepts everything, so a specific mailbox's existence is
genuinely unconfirmable without an SMTP RCPT probe. This system does not
attempt that: most cloud egress blocks port 25, and the capable open-source
prober (Reacher) is AGPL-licensed, which would force this whole codebase
open under embed-and-serve terms. `risky` leads still get emailed — they're
just deprioritized and watched more closely for bounces.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import dns.asyncresolver
import dns.resolver
import pandas as pd
from email_validator import EmailNotValidError
from email_validator import validate_email as _validate_email_syntax

from cold_mailer.contracts.a0_ingest import IngestOutput, RawLeadRow, ValidationDetail, Validity
from cold_mailer.core.logging import get_logger
from cold_mailer.quality.deny_lists import DISPOSABLE_DOMAINS, ROLE_ACCOUNT_LOCAL_PARTS

log = get_logger(component="a0_ingest", agent="A0")

# Column names we'll try, in order, when the sheet's header doesn't say
# "email" outright. The source spreadsheet has been collected over time by
# hand, so header drift is expected.
_EMAIL_COLUMN_CANDIDATES = ("email", "e-mail", "email address", "recruiter email", "contact email")
_NAME_COLUMN_CANDIDATES = ("name", "recruiter", "recruiter name", "contact", "contact name")

_mx_cache: dict[str, tuple[bool, str]] = {}


def _find_column(columns: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    lower_map = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    # fallback: substring match
    for col_lower, original in lower_map.items():
        if any(cand in col_lower for cand in candidates):
            return original
    return None


def load_rows(path: Path) -> list[RawLeadRow]:
    """Load an .xlsx/.xls/.csv file into normalized rows. Raises ValueError
    with a clear message if no email-like column can be found — better than
    silently ingesting garbage."""
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str)

    email_col = _find_column(df.columns, _EMAIL_COLUMN_CANDIDATES)
    if email_col is None:
        raise ValueError(
            f"Could not find an email column in {path.name}. Columns present: {list(df.columns)}"
        )
    name_col = _find_column(df.columns, _NAME_COLUMN_CANDIDATES)

    rows: list[RawLeadRow] = []
    for idx, record in df.iterrows():
        raw_email = record.get(email_col)
        if pd.isna(raw_email) or not str(raw_email).strip():
            continue
        extra = {
            str(k): str(v) for k, v in record.items() if k not in (email_col, name_col) and pd.notna(v)
        }
        rows.append(
            RawLeadRow(
                raw_email=str(raw_email).strip(),
                display_name=(str(record[name_col]).strip() if name_col and pd.notna(record.get(name_col)) else None),
                source_row=int(idx) + 2,  # +2: 1-indexed, plus header row
                extra=extra,
            )
        )
    return rows


async def _resolve_mx(domain: str) -> tuple[bool, str]:
    """Returns (has_mx, reason). Cached per-domain within a run — with
    ~900-1200 unique domains behind ~1800 leads, this halves DNS traffic
    outright, and callers should also persist the result on `companies` so
    a re-run of A0 doesn't re-resolve domains already known good."""
    if domain in _mx_cache:
        return _mx_cache[domain]

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 5.0
    try:
        answer = await resolver.resolve(domain, "MX")
        result = (len(answer) > 0, "mx_found")
    except dns.resolver.NXDOMAIN:
        result = (False, "nxdomain")
    except dns.resolver.NoAnswer:
        # No MX record. Some domains route mail via a bare A record instead —
        # rare but real for small self-hosted setups. Try that before giving up.
        try:
            await resolver.resolve(domain, "A")
            result = (True, "a_record_fallback")
        except Exception:
            result = (False, "no_mx_or_a")
    except Exception as exc:  # timeout, network error — inconclusive, not proof of invalidity
        log.warning("a0.mx_lookup_inconclusive", domain=domain, error=str(exc))
        result = (False, f"lookup_error:{type(exc).__name__}")

    _mx_cache[domain] = result
    return result


async def validate_row(raw: RawLeadRow) -> IngestOutput:
    email = raw.raw_email.strip().lower()

    try:
        validated = _validate_email_syntax(email, check_deliverability=False)
        normalized = validated.normalized
        syntax_ok = True
    except EmailNotValidError as exc:
        return IngestOutput(
            email=email,
            domain=email.split("@")[-1] if "@" in email else "",
            display_name=raw.display_name,
            validity=Validity.invalid,
            detail=ValidationDetail(
                syntax_ok=False, has_mx=False, is_disposable=False,
                is_role_account=False, reason=f"bad_syntax:{exc}",
            ),
        )

    local_part, domain = normalized.split("@", 1)
    is_disposable = domain in DISPOSABLE_DOMAINS
    is_role = local_part.lower() in ROLE_ACCOUNT_LOCAL_PARTS

    has_mx, mx_reason = await _resolve_mx(domain)

    if is_disposable or not has_mx:
        validity = Validity.invalid
    elif is_role:
        # Still deliverable, just lower-value — risky, not invalid.
        validity = Validity.risky
    else:
        validity = Validity.valid

    reason = mx_reason if not has_mx else ("disposable_domain" if is_disposable else
              ("role_account" if is_role else "ok"))

    return IngestOutput(
        email=normalized,
        domain=domain,
        display_name=raw.display_name,
        validity=validity,
        detail=ValidationDetail(
            syntax_ok=syntax_ok, has_mx=has_mx, is_disposable=is_disposable,
            is_role_account=is_role, is_catch_all=None, reason=reason,
        ),
    )


def dedupe(rows: list[RawLeadRow]) -> list[RawLeadRow]:
    """Keeps the first occurrence of each normalized email; later rows are
    dropped from the pipeline entirely (not just flagged) since a duplicate
    contact must never receive two independent outreach threads."""
    seen: dict[str, int] = {}
    unique: list[RawLeadRow] = []
    for row in rows:
        key = row.raw_email.strip().lower()
        if key in seen:
            log.info("a0.duplicate_dropped", email=key, first_seen_row=seen[key], dupe_row=row.source_row)
            continue
        seen[key] = row.source_row
        unique.append(row)
    return unique


async def ingest_file(path: Path) -> list[IngestOutput]:
    rows = dedupe(load_rows(path))
    log.info("a0.loaded", file=str(path), row_count=len(rows))
    results = [await validate_row(r) for r in rows]
    counts = {v.value: sum(1 for r in results if r.validity == v) for v in Validity}
    log.info("a0.validated", **counts)
    return results
