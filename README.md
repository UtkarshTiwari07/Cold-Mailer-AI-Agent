# Cold-Mailer-AI-Agent

An autonomous multi-agent pipeline that turns a recruiter's email address
into a deeply researched, personalized, human-approved cold outreach —
covering company research, job discovery, fit analysis, email generation
with a deterministic QA gate, delivery with deliverability safety rails,
and reply/learning feedback.

**Start here: [`DESIGN.md`](./DESIGN.md)** — the full technical design
document (architecture, agent roles, database schema, prompt strategy,
error/retry/caching strategy, security, deployment, and roadmap).

## Quickstart

```bash
cp .env.example .env          # fill in DEEPSEEK_API_KEY to use real LLM calls (optional — runs in stub mode without one)
uv pip install -e .           # or: pip install -e .
make up                       # postgres + redis + searxng, then runs migrations
make demo                     # ingest the sample leads and walk the whole pipeline, console transport
make web                      # approval queue at http://localhost:8000
make worker                   # background worker, processes the Postgres-backed task queue
```

See `DESIGN.md` §15-16 for the full deployment picture and what's built
today vs. the scaling roadmap.
