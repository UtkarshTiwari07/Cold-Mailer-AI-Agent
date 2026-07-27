-- Core schema.
--
-- Design notes:
--   * Status columns are TEXT + CHECK rather than ENUM. Postgres enums are
--     painful to evolve and this pipeline will grow stages.
--   * `tasks` is the work queue. Claiming uses SELECT ... FOR UPDATE SKIP LOCKED,
--     which makes the row itself the checkpoint: restart the worker and it
--     resumes, with no separate checkpoint store to fall out of sync.
--   * Research is keyed on `companies`, not `leads`. ~1800 recruiter emails
--     collapse to ~900-1200 domains, so A1-A4 run once per company and every
--     lead at that domain shares the result.

CREATE TABLE IF NOT EXISTS companies (
    id                  BIGSERIAL PRIMARY KEY,
    domain              TEXT NOT NULL UNIQUE,
    name                TEXT,
    website             TEXT,
    careers_url         TEXT,
    ats_type            TEXT,
    ats_token           TEXT,
    profile             JSONB,
    intel               JSONB,
    classification      JSONB,
    status              TEXT NOT NULL DEFAULT 'new'
                          CHECK (status IN ('new','researching','researched','failed','skipped')),
    research_confidence REAL,
    last_researched_at  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS companies_status_idx ON companies (status);

CREATE TABLE IF NOT EXISTS leads (
    id             BIGSERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    raw_email      TEXT,
    display_name   TEXT,
    domain         TEXT NOT NULL,
    company_id     BIGINT REFERENCES companies (id) ON DELETE SET NULL,
    source_file    TEXT,
    source_row     INTEGER,
    source_meta    JSONB,
    validity       TEXT CHECK (validity IN ('valid','risky','invalid')),
    validation     JSONB,
    status         TEXT NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new','validated','researching','ready','drafting',
                                       'awaiting_approval','approved','rejected','scheduled',
                                       'sent','replied','bounced','suppressed','failed')),
    suppressed_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS leads_status_idx     ON leads (status);
CREATE INDEX IF NOT EXISTS leads_domain_idx     ON leads (domain);
CREATE INDEX IF NOT EXISTS leads_company_id_idx ON leads (company_id);

-- Every factual claim an email makes about a company must point at a row here.
CREATE TABLE IF NOT EXISTS evidence (
    id           BIGSERIAL PRIMARY KEY,
    company_id   BIGINT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    url          TEXT,
    kind         TEXT NOT NULL,
    title        TEXT,
    text         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, content_hash)
);
CREATE INDEX IF NOT EXISTS evidence_company_idx ON evidence (company_id);

CREATE TABLE IF NOT EXISTS jobs (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    ats_type    TEXT,
    ats_job_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    location    TEXT,
    department  TEXT,
    url         TEXT,
    description TEXT,
    posted_at   TIMESTAMPTZ,
    raw         JSONB,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, ats_job_id)
);
CREATE INDEX IF NOT EXISTS jobs_company_idx ON jobs (company_id);

CREATE TABLE IF NOT EXISTS fit_analyses (
    id              BIGSERIAL PRIMARY KEY,
    lead_id         BIGINT NOT NULL UNIQUE REFERENCES leads (id) ON DELETE CASCADE,
    company_id      BIGINT REFERENCES companies (id) ON DELETE CASCADE,
    score           INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    tier            TEXT NOT NULL CHECK (tier IN ('high','medium','low')),
    angle           TEXT,
    rationale       TEXT,
    hooks           JSONB,
    gaps            JSONB,
    matched_job_ids BIGINT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fit_score_idx ON fit_analyses (score DESC);

-- touch 1 = opening email, 2 = first follow-up, 3 = second follow-up
CREATE TABLE IF NOT EXISTS drafts (
    id              BIGSERIAL PRIMARY KEY,
    lead_id         BIGINT NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    touch           INTEGER NOT NULL DEFAULT 1,
    subject         TEXT NOT NULL,
    subject_options JSONB,
    body            TEXT NOT NULL,
    linkedin_note   TEXT,
    citations       JSONB,
    lint_report     JSONB,
    lint_passed     BOOLEAN NOT NULL DEFAULT FALSE,
    grounded        BOOLEAN NOT NULL DEFAULT FALSE,
    model           TEXT,
    prompt_version  TEXT,
    regen_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','awaiting_approval','approved','rejected','sent')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lead_id, touch)
);
CREATE INDEX IF NOT EXISTS drafts_status_idx ON drafts (status);

-- reject_category is the highest-signal training data the system produces.
CREATE TABLE IF NOT EXISTS approvals (
    id              BIGSERIAL PRIMARY KEY,
    draft_id        BIGINT NOT NULL REFERENCES drafts (id) ON DELETE CASCADE,
    decision        TEXT NOT NULL CHECK (decision IN ('approve','edit','reject')),
    edited_subject  TEXT,
    edited_body     TEXT,
    reject_category TEXT,
    reject_reason   TEXT,
    decided_by      TEXT,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- UNIQUE(lead_id, touch) is the hard guarantee against double-sending.
CREATE TABLE IF NOT EXISTS messages (
    id                  BIGSERIAL PRIMARY KEY,
    lead_id             BIGINT NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    draft_id            BIGINT REFERENCES drafts (id) ON DELETE SET NULL,
    touch               INTEGER NOT NULL DEFAULT 1,
    transport           TEXT NOT NULL,
    provider_message_id TEXT,
    provider_thread_id  TEXT,
    from_email          TEXT,
    to_email            TEXT NOT NULL,
    subject             TEXT,
    body                TEXT,
    status              TEXT NOT NULL DEFAULT 'sent'
                          CHECK (status IN ('sent','failed','bounced','replied')),
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lead_id, touch)
);
CREATE INDEX IF NOT EXISTS messages_thread_idx ON messages (provider_thread_id);
CREATE INDEX IF NOT EXISTS messages_sent_at_idx ON messages (sent_at);

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    lead_id     BIGINT REFERENCES leads (id) ON DELETE CASCADE,
    message_id  BIGINT REFERENCES messages (id) ON DELETE CASCADE,
    type        TEXT NOT NULL
                  CHECK (type IN ('sent','bounced','replied','auto_reply','unsubscribed','failed')),
    payload     JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_type_idx ON events (type, occurred_at);

CREATE TABLE IF NOT EXISTS suppressions (
    id         BIGSERIAL PRIMARY KEY,
    scope      TEXT NOT NULL CHECK (scope IN ('email','domain')),
    value      TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scope, value)
);

-- Per-attempt audit ledger. Also the raw material for Agent 9 and for
-- answering "which leads are stuck at A3 with confidence below 0.5".
CREATE TABLE IF NOT EXISTS stage_runs (
    id           BIGSERIAL PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('lead','company','global')),
    subject_id   BIGINT,
    agent        TEXT NOT NULL,
    attempt      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL CHECK (status IN ('running','ok','error','skipped')),
    confidence   REAL,
    input_hash   TEXT,
    output       JSONB,
    error        TEXT,
    model        TEXT,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost_usd     NUMERIC(12, 6),
    latency_ms   INTEGER,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS stage_runs_subject_idx ON stage_runs (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS stage_runs_agent_idx   ON stage_runs (agent, status);

-- Keyed on schema_version so bumping a contract invalidates stale rows
-- instead of silently returning output that no longer validates.
CREATE TABLE IF NOT EXISTS llm_cache (
    prompt_hash    TEXT NOT NULL,
    model          TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    response       JSONB NOT NULL,
    tokens_in      INTEGER,
    tokens_out     INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_hash, model, schema_version)
);

-- The work queue.
--   dedupe_key UNIQUE  -> enqueue is idempotent, so a retried producer cannot
--                         create duplicate work.
--   run_after          -> delayed jobs, which is how follow-up touches are
--                         scheduled days ahead without a separate scheduler.
CREATE TABLE IF NOT EXISTS tasks (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('lead','company','global')),
    subject_id   BIGINT,
    payload      JSONB,
    status       TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','claimed','done','error','dead')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    priority     INTEGER NOT NULL DEFAULT 100,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at   TIMESTAMPTZ,
    claimed_by   TEXT,
    last_error   TEXT,
    dedupe_key   TEXT UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_claim_idx
    ON tasks (status, run_after, priority, id)
    WHERE status = 'pending';

-- One row per calendar day. The warm-up ramp reads and writes this.
CREATE TABLE IF NOT EXISTS send_budget (
    day       DATE PRIMARY KEY,
    sent      INTEGER NOT NULL DEFAULT 0,
    cap       INTEGER NOT NULL,
    halted    BOOLEAN NOT NULL DEFAULT FALSE,
    halt_note TEXT
);
