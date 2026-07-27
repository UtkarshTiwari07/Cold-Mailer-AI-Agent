"""Command-line entrypoints. Thin wrappers around the same functions the
worker and web UI use — no pipeline logic lives here, only orchestration
glue and printing.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from cold_mailer.agents.a0_ingest import ingest_file
from cold_mailer.agents.a5_fit import synthesize_fit
from cold_mailer.agents.a7_generate import generate_draft
from cold_mailer.agents.a8_deliver import deliver
from cold_mailer.agents.company_research import research_company
from cold_mailer.contracts.a0_ingest import Validity
from cold_mailer.core.config import get_settings
from cold_mailer.core.db import acquire, close_pool, get_pool
from cold_mailer.core.logging import configure_logging, get_logger
from cold_mailer.pipeline.state_machine import enqueue_task

app = typer.Typer(add_completion=False)
log = get_logger(component="cli")


async def _ingest_to_db(path: Path) -> dict:
    """A0's output, persisted: valid/risky leads get a company row and join
    the research queue; invalid ones are recorded as suppressed rather than
    silently dropped, so `SELECT * FROM leads WHERE status='suppressed'`
    shows exactly what was filtered and why."""
    results = await ingest_file(path)
    counts = {"valid": 0, "risky": 0, "invalid": 0}
    domains_touched: set[str] = set()

    for r in results:
        counts[r.validity.value] += 1
        async with acquire() as conn:
            if r.validity == Validity.invalid:
                await conn.execute(
                    "INSERT INTO leads (email, domain, display_name, validity, validation, status, suppressed_at) "
                    "VALUES ($1,$2,$3,$4,$5::jsonb,'suppressed',now()) "
                    "ON CONFLICT (email) DO UPDATE SET validity = EXCLUDED.validity, validation = EXCLUDED.validation",
                    r.email, r.domain, r.display_name, r.validity.value, r.detail.model_dump_json(),
                )
                continue

            company = await conn.fetchrow(
                "INSERT INTO companies (domain) VALUES ($1) "
                "ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain RETURNING id",
                r.domain,
            )
            await conn.execute(
                "INSERT INTO leads (email, domain, display_name, company_id, validity, validation, status) "
                "VALUES ($1,$2,$3,$4,$5,$6::jsonb,'validated') "
                "ON CONFLICT (email) DO UPDATE SET company_id = EXCLUDED.company_id, "
                "validity = EXCLUDED.validity, validation = EXCLUDED.validation, status = 'validated'",
                r.email, r.domain, r.display_name, company["id"], r.validity.value, r.detail.model_dump_json(),
            )
        domains_touched.add(r.domain)

    for domain in domains_touched:
        await enqueue_task(
            "research_company", "global", None, {"domain": domain},
            dedupe_key=f"research_company:{domain}",
        )

    return {**counts, "distinct_companies": len(domains_touched)}


@app.command()
def ingest(file: str = "data/sample_leads.csv") -> None:
    """Load a lead spreadsheet, validate, dedupe, and enqueue research —
    the real path a worker (`make worker`) then processes asynchronously."""
    configure_logging(get_settings().obs.log_level, get_settings().obs.log_json)

    async def _main() -> None:
        await get_pool()
        result = await _ingest_to_db(Path(file))
        print(json.dumps(result, indent=2))
        await close_pool()

    asyncio.run(_main())


@app.command()
def seed(file: str = "data/sample_leads.csv") -> None:
    """Alias for `ingest` against the bundled sample file — `make seed`."""
    ingest(file)


@app.command()
def run(limit: int = 5, transport: str = "console", file: str = "data/sample_leads.csv") -> None:
    """End-to-end walkthrough: ingest -> research -> fit -> generate -> send,
    for up to `limit` leads, run synchronously (no task queue/worker
    involved) so it reads top to bottom as a tour of the pipeline. This is
    what `make demo` calls — not a production entrypoint, and it does not
    override the safety rails: outside configured business hours, the send
    step correctly reports `skipped_budget` rather than forcing a send."""
    os.environ["CM_DELIVERY__TRANSPORT"] = transport
    configure_logging(get_settings().obs.log_level, get_settings().obs.log_json)

    async def _main() -> None:
        await get_pool()
        ingest_result = await _ingest_to_db(Path(file))
        print("Ingest:", json.dumps(ingest_result, indent=2))

        async with acquire() as conn:
            leads = await conn.fetch(
                "SELECT id, email, domain FROM leads WHERE status = 'validated' ORDER BY id LIMIT $1", limit
            )

        researched_domains: set[str] = set()
        for lead in leads:
            if lead["domain"] not in researched_domains:
                print(f"\n--- Researching {lead['domain']} ---")
                await research_company(lead["domain"])
                researched_domains.add(lead["domain"])

            print(f"\n--- Fit + draft for {lead['email']} ---")
            await synthesize_fit(lead["domain"], lead["id"])
            gen = await generate_draft(lead["domain"], lead["id"], touch=1)

            async with acquire() as conn:
                draft_row = await conn.fetchrow(
                    "SELECT status, subject FROM drafts WHERE lead_id = $1 AND touch = 1", lead["id"]
                )

            if draft_row and draft_row["status"] == "awaiting_approval":
                result = await deliver(
                    lead["id"], 1, lead["email"], draft_row["subject"], gen.draft.body
                )
                print(f"--- Delivery for {lead['email']}: {result.status.value} ---")
            else:
                print(f"--- Draft for {lead['email']} needs human review before sending (QA not clean) ---")

        await close_pool()

    asyncio.run(_main())


@app.command()
def report() -> None:
    """Print Agent 9's learning report (reply/bounce rates by subject,
    prompt version, category, relevance tier)."""
    configure_logging(get_settings().obs.log_level, get_settings().obs.log_json)

    async def _main() -> None:
        from cold_mailer.agents.a9_learn import generate_report

        await get_pool()
        result = await generate_report()
        print(result.model_dump_json(indent=2))
        await close_pool()

    asyncio.run(_main())


if __name__ == "__main__":
    app()
