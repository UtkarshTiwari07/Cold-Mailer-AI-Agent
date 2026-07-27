"""The approval queue: the one required human checkpoint before anything is
sent (per the system requirements' "human approval before sending"). A
lead's `send_message` task is only ever enqueued from the `/approve` route
below — nowhere else in the codebase creates one — so a draft physically
cannot reach Agent 8 without a human clicking Approve first.

Server-rendered Jinja, no JS build step: this is an internal review tool
for a single operator, not a product surface, and a build pipeline would be
pure overhead here.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from cold_mailer.core.config import get_settings
from cold_mailer.core.db import acquire, close_pool, get_pool
from cold_mailer.core.logging import configure_logging, get_logger
from cold_mailer.pipeline.state_machine import enqueue_task

log = get_logger(component="web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(get_settings().obs.log_level, get_settings().obs.log_json)
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Cold Mailer — Approval Queue", lifespan=lifespan)


@app.get("/")
async def queue(request: Request):
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT l.id AS lead_id, l.email, l.domain, l.display_name,
                   c.name AS company_name,
                   f.score, f.tier, f.angle,
                   d.subject, d.body, d.lint_passed, d.grounded, d.regen_count
            FROM leads l
            JOIN drafts d ON d.lead_id = l.id AND d.touch = 1
            LEFT JOIN companies c ON c.id = l.company_id
            LEFT JOIN fit_analyses f ON f.lead_id = l.id
            WHERE l.status = 'awaiting_approval'
            ORDER BY f.score DESC NULLS LAST, l.id
            """
        )
    return templates.TemplateResponse(request, "queue.html", {"leads": [dict(r) for r in rows]})


@app.get("/drafts/{lead_id}")
async def draft_detail(request: Request, lead_id: int):
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT l.id AS lead_id, l.email, l.domain, l.display_name, l.status AS lead_status,
                   c.name AS company_name, c.profile, c.intel, c.classification,
                   f.score, f.tier, f.angle, f.rationale, f.hooks, f.gaps,
                   d.id AS draft_id, d.subject, d.subject_options, d.body, d.linkedin_note,
                   d.citations, d.lint_report, d.lint_passed, d.grounded, d.regen_count, d.status AS draft_status
            FROM leads l
            JOIN drafts d ON d.lead_id = l.id AND d.touch = 1
            LEFT JOIN companies c ON c.id = l.company_id
            LEFT JOIN fit_analyses f ON f.lead_id = l.id
            WHERE l.id = $1
            """,
            lead_id,
        )
    if row is None:
        return RedirectResponse("/", status_code=303)

    data = dict(row)
    for key in ("subject_options", "citations", "lint_report", "hooks", "gaps"):
        if data.get(key) and isinstance(data[key], str):
            data[key] = json.loads(data[key])
    return templates.TemplateResponse(request, "draft.html", {"lead": data})


@app.post("/drafts/{lead_id}/approve")
async def approve(lead_id: int):
    async with acquire() as conn:
        draft = await conn.fetchrow(
            "SELECT id, subject, body FROM drafts WHERE lead_id = $1 AND touch = 1", lead_id
        )
        lead = await conn.fetchrow("SELECT email FROM leads WHERE id = $1", lead_id)
        if draft is None or lead is None:
            return RedirectResponse("/", status_code=303)

        await conn.execute("UPDATE drafts SET status = 'approved' WHERE id = $1", draft["id"])
        await conn.execute("UPDATE leads SET status = 'approved', updated_at = now() WHERE id = $1", lead_id)
        await conn.execute(
            "INSERT INTO approvals (draft_id, decision, decided_by) VALUES ($1, 'approve', 'operator')",
            draft["id"],
        )

    # This is the ONLY place in the codebase that enqueues send_message —
    # the enforcement point for "human approval before sending".
    await enqueue_task(
        "send_message", "lead", lead_id,
        {"to_email": lead["email"], "subject": draft["subject"], "body": draft["body"], "touch": 1},
        dedupe_key=f"send_message:{lead_id}:1",
    )
    log.info("web.approved", lead_id=lead_id)
    return RedirectResponse("/", status_code=303)


@app.post("/drafts/{lead_id}/reject")
async def reject(lead_id: int, reject_category: str = Form(...), reject_reason: str = Form("")):
    async with acquire() as conn:
        draft = await conn.fetchrow("SELECT id FROM drafts WHERE lead_id = $1 AND touch = 1", lead_id)
        if draft is None:
            return RedirectResponse("/", status_code=303)
        await conn.execute("UPDATE drafts SET status = 'rejected' WHERE id = $1", draft["id"])
        await conn.execute("UPDATE leads SET status = 'rejected', updated_at = now() WHERE id = $1", lead_id)
        await conn.execute(
            "INSERT INTO approvals (draft_id, decision, reject_category, reject_reason, decided_by) "
            "VALUES ($1, 'reject', $2, $3, 'operator')",
            draft["id"], reject_category, reject_reason,
        )
    log.info("web.rejected", lead_id=lead_id, category=reject_category)
    return RedirectResponse("/", status_code=303)


@app.post("/drafts/{lead_id}/edit")
async def edit(lead_id: int, subject: str = Form(...), body: str = Form(...)):
    """Edit-then-approve in one step: the edited copy is what gets sent and
    what's recorded in `approvals.edited_*` — the signal Agent 9 needs to
    learn which of A7's drafts needed a human's hand and which didn't."""
    async with acquire() as conn:
        draft = await conn.fetchrow("SELECT id FROM drafts WHERE lead_id = $1 AND touch = 1", lead_id)
        lead = await conn.fetchrow("SELECT email FROM leads WHERE id = $1", lead_id)
        if draft is None or lead is None:
            return RedirectResponse("/", status_code=303)
        await conn.execute(
            "UPDATE drafts SET subject = $1, body = $2, status = 'approved' WHERE id = $3",
            subject, body, draft["id"],
        )
        await conn.execute("UPDATE leads SET status = 'approved', updated_at = now() WHERE id = $1", lead_id)
        await conn.execute(
            "INSERT INTO approvals (draft_id, decision, edited_subject, edited_body, decided_by) "
            "VALUES ($1, 'edit', $2, $3, 'operator')",
            draft["id"], subject, body,
        )

    await enqueue_task(
        "send_message", "lead", lead_id,
        {"to_email": lead["email"], "subject": subject, "body": body, "touch": 1},
        dedupe_key=f"send_message:{lead_id}:1",
    )
    log.info("web.edited_and_approved", lead_id=lead_id)
    return RedirectResponse("/", status_code=303)
