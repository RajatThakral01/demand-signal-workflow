# Demand-Signal Scoring, Routing & Attribution Workflow

**Evaluation ID:** `DAXVORA-RAJAT-2026-08-A01`  
**Status:** Phase 11 complete — documentation finalized, Phase 12 (final packaging) pending  
**Stack:** Python 3.11+ · FastAPI · Postgres 15 · SQLAlchemy 2.0 (async) · Pydantic v2 · OpenRouter / Groq (live LLM) · Docker Compose

A backend that takes messy, duplicated demand signals — a web form fill, a social mention, an email reply — and turns them into **exactly one trusted lead per real person**, with a score, a queue, and proof that nothing was lost or double-counted. Plain language, deterministic rules, and a dashboard that reconciles to receipts.

> **New here?** Start at [Setup for a new environment](#setup-for-a-new-environment) — one `cp .env.example .env` + `docker compose up -d` and you are running.

---

## Table of contents

- [The problem this solves](#the-problem-this-solves)
- [What it actually does](#what-it-actually-does)
- [Simulated vs. real — what's actually live](#simulated-vs-real--whats-actually-live)
- [Architecture — clean, simple, and obvious](#architecture--clean-simple-and-obvious)
- [Request lifecycle (with examples)](#request-lifecycle-with-examples)
- [File structure](#file-structure)
- [Deep file guide — what each file does (simple language)](#deep-file-guide--what-each-file-does-simple-language)
- [Setup for a new environment](#setup-for-a-new-environment)
- [Configuration — environment variables](#configuration--environment-variables)
- [Running the app — health, seed, dashboard](#running-the-app--health-seed-dashboard)
- [Using the API](#using-the-api)
- [Running tests](#running-tests)
- [Dashboard guide](#dashboard-guide)
- [Cost & limits (LLM)](#cost--limits-llm)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [More detail](#more-detail)

---

## The problem this solves

Founder-led businesses get interest from many disconnected places: a website form, a mention in a community, someone opening a marketing email. Without a system:

- The same person becomes three different contacts.
- Nobody scores leads the same way twice.
- Retries and duplicate submissions create duplicate leads.
- You cannot prove where a lead actually came from.

This project fixes that with **one deterministic pipeline** that any signal source flows through. Same input → same output, every time.

---

## What it actually does

1. **Ingests** a signal from any of three sources (see next section).
2. **Resolves identity** — matches to an existing person or creates a new one. If unsure, it parks the event for a human instead of guessing.
3. **Interprets intent** — a real LLM call reads the message and labels it (`pricing_inquiry`, `product_question`, etc.) with confidence.
4. **Scores** using a **versioned file** (`app/policies/scoring_policy_v1.json`), not hidden code. Explainable: `score + features + decision (hot/warm/cold/needs_review)`.
5. **Routes** to a queue via an explicit rule table with fallback and SLA deadlines. Escalation is computed on read.
6. **Proves it** — every count on the dashboard traces to a stored `receipt`. `GET /api/v1/dashboard/reconciliation` must show `variance: 0`.

Edits, duplicates, ambiguous matches, and LLM failures are handled explicitly — never silently dropped.

---

## Simulated vs. real — what's actually live

| Part | Status | What that means |
|---|---|---|
| The three signal sources (web form, social mention, email engagement) | **Simulated** | Fixture JSON files replayed against `POST /api/v1/events`. No real Reddit/social/email API is called. |
| LLM classification call | **Live** | Real call to **OpenRouter** (default) or **Groq** for every event that needs interpreting. Not mocked in normal use. |
| Identity, scoring, routing, attribution | **Deterministic local logic** | Versioned JSON policy files only. No external service. |
| Automated test suite | **Mocked** | Tests fake the LLM so they run offline and cost nothing. |

Full measured numbers: [`docs/Cost_and_Limits.md`](docs/Cost_and_Limits.md).

---

## Architecture — clean, simple, and obvious

No microservices, no message broker, no frontend build. One FastAPI process + one Postgres.

### High-level diagram

```
  Signal sources (simulated)
  web_form · social_mention · email_engagement
              │
              ▼
        POST /api/v1/events          ← one endpoint, validated by Pydantic discriminated union
              │
              ▼
   ┌───────────────────────┐
   │  RESOLVE (FR-3)       │  → is this a known person?
   │  exact email / phone  │     yes (1.00 confidence) → link & continue
   │  fuzzy name+company → │     unsure → manual_review queue (pipeline halts here)
   │  manual review        │
   └───────────────────────┘
              │
              ▼
   ┌───────────────────────┐
   │  INTERPRET (FR-4)     │  → what does the text mean?
   │  LIVE LLM call        │     label + confidence + reason; <2 tokens → unknown (no call)
   │  bounded retry →      │     on failure → dead_letter queue (visible, replayable)
   └───────────────────────┘
              │
              ▼
   ┌───────────────────────┐
   │  SCORE (FR-5)         │  → how hot is this lead?
   │  versioned JSON file  │     unknown → needs_review, no score math
   └───────────────────────┘
              │
              ▼
   ┌───────────────────────┐
   │  ACT (FR-6/7/8)       │  → create/update exactly ONE lead per identity
   │  lead + route +       │     route via rule table + SLA deadline
   │  attribution          │     first-touch immutable, last-touch = latest
   └───────────────────────┘
              │
              ▼
     Dashboard + reconciliation (FR-9/10)
     every number → receipt → DB row, variance must be 0
```

### Layers (clean architecture)

```
routers/  →  services/  →  db/models + session  →  Postgres
   ↑            ↑                ↑
schemas/   policies/*.json   receipts (audit)
(Pydantic) (no code change) (every mutation → receipt, same transaction)
```

- **Routers** are thin: validate, call a service, return JSON/HTML. No business logic.
- **Services** are pure + DB-aware, one file per step: `ingest.py`, `resolve.py`, `interpret.py`, `score.py`, `act.py`, `attribute.py`, etc. Each owns its receipts.
- **DB** enforces truth: uniqueness is on the **database** (partial indexes), not just `if exists` in Python, so retries/concurrency cannot create duplicates. One commit = one business action + its receipt(s).
- **Policies** are JSON files, versioned and inspected — change rules without touching code.
- **Receipts** are the audit log. Reconciliation recomputes counts from `receipts` and compares to entity tables.

### Why this shape

- Single container + single DB = reproducible in <5 minutes (PRD Success Criterion 4).
- DB-level constraints make idempotency survive crashes and parallel requests — app-level checks alone would race.
- No broker needed at this scale; `dead_letter_queue` table is the durable queue (explicit v1 choice).

---

## Request lifecycle (with examples)

**1. Clean new signal → lead created (Flow 1):** valid `web_form` arrives → `dedupe_key = hash(source|external_event_id)` new → identity: no email match → new `identities` row → text ≥2 tokens → live LLM returns `pricing_inquiry 0.86` → policy: 85*0.86 + bonuses → `warm` → `leads` (status `routed`) + `routes` (e.g. `sales_default`, SLA 24h) + `attribution_touches` (first=last=this event) + 5 receipts, all same commit.

**2. Duplicate / retry (Flow 2):** same `source|external_event_id` and same `payload_hash` → found via `uq_events_dedupe_key_valid` → return `{duplicate:true}` — **no new rows, no new receipts, no re-scoring**.

**3. Edited resubmission (Flow 2 edge):** same `dedupe_key` but different `payload_hash` → update `raw_payload`/`identity_fields`, set `is_edit=true`, advance `payload_hash`, write `event_edited` receipt, **re-run** resolve→interpret→score→act (updates in place via `ON CONFLICT`/`lead.identity_id` unique).

**4. Ambiguous identity (Flow 3):** name-only `social_mention` like “Ada Solo” fuzzy-matches existing “Ada Lovelace” → no auto-merge (threshold 0.85 is only a reviewer hint) → `manual_review_queue` row `{status:pending, reason: fuzzy_name_company_manual_review:0.88}` → pipeline **halts**. Reviewer `POST /api/v1/manual-review/{id}/resolve` with `create_new` or `merge_into` → resumes interpret→score→act through the **same** `pipeline.run_downstream` runner.

**5. LLM failure (Flow 4):** timeout/429/5xx → `tenacity` retries (`RETRY_MAX_ATTEMPTS` 3, exponential + jitter). On exhaustion → `dead_letter_queue` row + `dead_lettered` receipt (atomic), response `202 {status:dead_letter, stage:interpret}`. Visible at `GET /api/v1/dead-letter?resolved=false`, replayable via `POST /api/v1/admin/replay/{event_id}` (idempotent, Bearer `ADMIN_API_KEY`).

---

## File structure

```
project-root/
├── app/
│   ├── main.py                # FastAPI app, routers, /health, security headers
│   ├── config.py              # pydantic-settings, reads .env, validates required keys
│   ├── errors.py              # typed domain errors (e.g. MalformedJSONError)
│   ├── logging.py             # structlog JSON, PII redaction
│   ├── db/
│   │   ├── models.py           # 11 tables (SQLAlchemy) + constraints
│   │   ├── session.py          # async engine/session, check_db()
│   │   └── migrations/         # Alembic versions (env.py, script.py.mako, versions/)
│   ├── schemas/
│   │   ├── events.py           # discriminated union for 3 sources (Pydantic)
│   │   └── responses.py        # EventIngestResponse shape
│   ├── services/
│   │   ├── ingest.py           # hashing, dedupe_key/payload_hash, duplicate vs edit
│   │   ├── resolve.py          # identity: exact email/phone → auto-link, fuzzy → review
│   │   ├── interpret.py        # LIVE LLM call, <2-token skip, retry→dead-letter
│   │   ├── score.py            # policy application, unknown→needs_review, hot/warm/cold
│   │   ├── act.py              # lead create/update + route + SLA (one transaction)
│   │   ├── attribute.py        # first-touch immutable, last-touch latest, edit handling
│   │   ├── pipeline.py         # shared run_downstream (ingest/resume/replay use same code)
│   │   ├── receipts.py         # write_receipt (every mutation), VALID_ACTION_TYPES
│   │   ├── escalation.py       # is_sla_breached + evaluate_escalation (on-read)
│   │   └── summarize.py        # dashboard counts (fresh SQL, no cache)
│   ├── routers/
│   │   ├── events.py           # POST/GET /api/v1/events (FR-1/2)
│   │   ├── leads.py            # GET /api/v1/leads, /leads/{id} (filters, escalated)
│   │   ├── manual_review.py    # GET/POST /api/v1/manual-review (resolve + resume)
│   │   ├── dashboard.py        # GET /api/v1/dashboard/{summary,reconciliation}
│   │   ├── dead_letter.py      # GET /api/v1/dead-letter
│   │   ├── admin.py            # POST /api/v1/admin/{replay,simulate-failure} (Bearer)
│   │   └── pages.py            # HTML pages: /dashboard, /leads, /manual-review, /dead-letter
│   ├── policies/
│   │   ├── scoring_policy_v1.json    # label→score, bonuses, thresholds
│   │   ├── identity_policy_v1.json   # rules order + threshold 0.85
│   │   └── routing_rules_v1.json     # ordered rules + fallback
│   ├── templates/              # Jinja2 HTML (base, dashboard_summary, leads_list, etc.)
│   └── static/style.css        # plain CSS, no framework, polished evaluator tooling
├── fixtures/
│   ├── web_form_events.json
│   ├── social_mention_events.json
│   ├── email_engagement_events.json
│   └── generate_and_post.py    # "one seed command" — POSTs fixtures to running API
├── tests/
│   ├── conftest.py             # pins DATABASE_URL → dsw_test, fresh schema per test
│   ├── unit/                   # hashing, schemas, scoring, routing, helpers
│   └── integration/            # pipeline, dedupe, identity, reconciliation, dead-letter, etc.
├── docs/
│   ├── PRD_Demand_Signal_Workflow_v1_2.md
│   ├── Project02_Implementation_Plan_v1.md
│   ├── Cost_and_Limits.md      # measured LLM cost/latency, rate limits
│   ├── Plan_vs_Built.md
│   ├── Known_Limitations.md
│   └── evidence/               # logs/screenshots proving claims
├── scripts/clean_run.sh        # fresh DB + timed pytest (evaluator's clean run)
├── docker/
│   └── init/                   # init SQL: creates dsw_test on first boot
├── docker-compose.yml          # app + db (Postgres 15), healthcheck, env wiring
├── Dockerfile                  # python:3.11-slim, pip install, alembic, uvicorn
├── docker-entrypoint.sh        # runs alembic upgrade head then uvicorn
├── alembic.ini                 # Alembic config (URL from settings)
├── .env.example                # all env vars with defaults (copy to .env)
├── requirements.txt            # pinned deps (fastapi, sqlalchemy[asyncio], asyncpg, etc.)
├── AI_USAGE.md                 # full AI disclosure
└── ai-usage.json               # structured AI usage
```

---

## Deep file guide — what each file does (simple language)

This is the “read one paragraph per file and you get it” section. No jargon, just what it is and why you care.

### App core

| File | In plain language | When you touch it |
|---|---|---|
| `app/main.py` | Creates the FastAPI app, mounts all routers, serves `/static`, adds security headers, and exposes `GET /health` which does a real `SELECT 1` against Postgres (not a fake “ok”). | Add a new endpoint or middleware. |
| `app/config.py` | Single source of truth for settings. Reads `.env` via `pydantic-settings`. `ADMIN_API_KEY` has **no default** — app fails fast if missing. Handles empty-string `""` from `.env.example` → defaults. | Change env var names, defaults, or add a new setting. |
| `app/errors.py` | One typed error: `MalformedJSONError` → `400 {"error":"malformed_json"}`. Keeps error shapes consistent. | Add new domain errors. |
| `app/logging.py` | Structured `structlog` JSON logging, with PII redaction (email/phone/name never in logs). Used by every service. | Change log format or add fields. |

### Database

| File | Simple explanation |
|---|---|
| `app/db/models.py` | 11 tables, each with a short docstring. Key tricks: `events.dedupe_key` has a **partial** unique index `WHERE is_valid=true` (so a rejected event never blocks its corrected retry); `identities.primary_email/phone` also partial; `leads.identity_id` unique (one lead per person); `routes.lead_id` unique; `dead_letter_queue.event_id` unique `WHERE resolved=false`. This is why duplicates/edits/concurrency are safe even if two requests arrive at the same time. |
| `app/db/session.py` | Creates the async engine (`asyncpg`) lazily, gives you a session per request, and `check_db()` for `/health`. |
| `app/db/migrations/` | Alembic: `env.py` reads DB URL from settings at runtime, `versions/` holds each schema change. `alembic.ini` has no hard-coded URL. |

**Tables at a glance (what they store):** `events` (raw signal + hashes), `identities` + `identity_links` + `manual_review_queue` (who is who), `interpretations` (LLM label/confidence), `scores` (policy output), `leads` + `routes` + `attribution_touches` (the business outcome), `receipts` (audit for every mutation), `dead_letter_queue` (failed pipeline attempts).

### Schemas (validation)

| File | Simple explanation |
|---|---|
| `app/schemas/events.py` | Three Pydantic models (`WebFormEvent`, `SocialMentionEvent`, `EmailEngagementEvent`) in a **discriminated union** on `source`. One endpoint validates all three. `event_adapter` is the validator you use on raw JSON. |
| `app/schemas/responses.py` | The shape returned by `POST /api/v1/events` and friends: `event_id`, `is_valid`, `duplicate`, `status: linked/manual_review/dead_letter`, `identity_id`, `interpret_status`, `label`, `score/decision`, `lead_id/queue/rule_matched`, `attribution_touch_id`. Fields are optional because different branches return different subsets. |

### Services (the pipeline steps)

| File | What it does for a non-engineer | Key detail devs care about |
|---|---|---|
| `app/services/ingest.py` | Turns raw JSON into a stored event. Computes `dedupe_key = sha256(source|external_event_id)` and `payload_hash = sha256(canonical JSON)`. Same key + same hash → duplicate (no-op). Same key + different hash → edit (update + re-run pipeline, `event_edited` receipt). | `canonical_json` sorts keys so field order does not matter. DB unique index is the race guard; loser gets `IntegrityError` → treated as duplicate. `is_valid=false` events each get their own row + `event_rejected` receipt. |
| `app/services/resolve.py` | Finds the person. `normalize_email` (trim+lower), `normalize_phone` (digits, strip leading 1). Exact email → auto-link, else exact phone → auto-link, else fuzzy name+company via `difflib.SequenceMatcher` → **always** manual review (threshold 0.85 only picks a *candidate* to show the reviewer, never auto-merges). | `_link_via_exact` handles concurrent inserts via partial unique index + re-SELECT. `resolve_review` uses atomic `UPDATE ... WHERE status='pending'` so two concurrent resolves → one 200, one 409. |
| `app/services/interpret.py` | Calls the **live** LLM. If text < `INTERPRET_MIN_TOKENS` (default 2) → `unknown` **without** calling the provider (saves cost, `was_skipped=true`). Otherwise bounded retry (`tenacity`, `retry_if_exception` only 429/5xx/timeout/parse), then dead-letter + `dead_lettered` receipt, raise `InterpretError`. | `AsyncOpenAI` pointed at `openrouter.ai/api/v1` or `api.groq.com/openai/v1`. `temperature=0` (OpenRouter) / `1e-8` (Groq quirk). `max_tokens=400`. `_parse_classification` tolerates wrapped/truncated JSON. `token_usage` saved. |
| `app/services/score.py` | Applies `scoring_policy_v1.json`. First check: if `label_scores[label]` is `null` (i.e. `unknown`) → `needs_review, score=None` immediately, no math. Else `label_score * confidence` + source/consent/campaign bonuses → clamp 0–100 → decision via descending thresholds (≥ at boundary). | `pg_insert(...).on_conflict_do_update` upsert by `event_id` — concurrent replay safe. `features` always stored. |
| `app/services/act.py` | Creates or updates **one lead per identity** (`leads.identity_id` unique), routes via `routing_rules_v1.json` (`queue, rule_matched, sla_hours`), sets `sla_deadline = now + hours`. Falls back to `fallback_no_rule`. Inserts/updates `attribution_touches` too. **One commit** for lead+route+touch+3 receipts. | `route_lead` upserts by `lead_id`. `create_or_update_lead` handles `IntegrityError` race. `lead.status` goes `new → routed`. |
| `app/services/attribute.py` | Tracks `first_touch` (immutable, only strictly earlier `received_at` replaces) and `last_touch` (strictly later replaces; ties keep first). Edit (`is_edit=true`) only updates denormalized `source/campaign_id` on the touched row, no new row, no timestamp change. | `IntegrityError` race on `identity_id` unique → rollback + re-read. |
| `app/services/pipeline.py` | The **shared runner**: `run_downstream(event, identity_id)` → `classify → score → act`. Used by ingest, manual-review resume, and admin replay — so a replayed event is scored/routed by **identical** code. | All stages idempotent via DB uniques, so `run_downstream` twice → update in place. |
| `app/services/receipts.py` | One function: `write_receipt(action_type, entity_id, ...)` — never commits (caller owns tx). `VALID_ACTION_TYPES` is the allowlist (14 values); unknown → `ValueError` loud. | Receipt `metadata` + `status` store per-action detail for audit. |
| `app/services/escalation.py` | `sla_deadline < now()` → `routes.escalated = true` + `escalated` receipt. Computed **on read** (PRD §12 choice, no scheduler). First read that sees the breach persists it; later reads do nothing. | `is_sla_breached` is strict `<` (deadline exactly now = not breached). |
| `app/services/summarize.py` | Counts for dashboard: totals, by source/decision/status, pending reviews, dead letters. Fresh SQL per request, optional `since/until` window. | No cache — HTML badge and JSON stay consistent. |

### Routers (HTTP endpoints)

| File | Endpoint(s) | Plain language |
|---|---|---|
| `app/routers/events.py` | `POST /api/v1/events`, `GET /api/v1/events/{id}` | Accepts any source, validates, handles malformed JSON `400`, `is_valid=false` isolated, duplicate no-op, edit, then resolve→interpret→score→act. `GET` returns event + latest score. |
| `app/routers/leads.py` | `GET /api/v1/leads`, `GET /api/v1/leads/{id}` | List/filter by `status/source/decision`, detail with route+score+attribution. Computes `escalated` on read. |
| `app/routers/manual_review.py` | `GET /api/v1/manual-review`, `POST /api/v1/manual-review/{id}/resolve` | Lists parked events, resolves (`merge_into` needs `identity_id`, `create_new` mints one) and resumes pipeline; if interpret fails → `202 dead_letter`. |
| `app/routers/dashboard.py` | `GET /api/v1/dashboard/{summary,reconciliation}` | `summary` = counts; `reconciliation` = entity counts vs `receipts` counts per `_PAIRS`, `variance` must be 0. No caching. |
| `app/routers/dead_letter.py` | `GET /api/v1/dead-letter?resolved=false` | Worklist for retries, oldest first, with `replay_url`. |
| `app/routers/admin.py` | `POST /api/v1/admin/replay/{id}`, `POST /api/v1/admin/simulate-failure` | Bearer `ADMIN_API_KEY` only. Replay re-runs pipeline idempotently; simulate-failure dead-letters an event for testing (race-safe 409). |
| `app/routers/pages.py` | `GET /`, `GET /dashboard`, `/dashboard/leads`, `/dashboard/manual-review`, `/dashboard/dead-letter` | Server-rendered Jinja2, plain HTML/CSS, no JS build. `/dashboard/manual-review/{id}/resolve` HTML form mirrors the JSON logic. |

### Policies (rules outside code)

| File | What you can change without code |
|---|---|
| `app/policies/scoring_policy_v1.json` | `label_scores` (pricing_inquiry 85 etc., `unknown:null`), `source_bonus`, `consent_bonus`, `campaign_bonus`, `confidence_multiplier_enabled`, `decision_thresholds` (`hot ≥70, warm ≥45, cold ≥20`). |
| `app/policies/identity_policy_v1.json` | `rules_order: [exact_email, exact_phone, fuzzy_name_company]`, `confidence_threshold: 0.85`, algorithm note (`difflib.SequenceMatcher.ratio` average). |
| `app/policies/routing_rules_v1.json` | Ordered `rules` (`hot_any → sales_urgent 2h`, `warm_pricing → sales_priority 8h`, ...), `fallback: unassigned 72h`, every route records `rule_matched`. |

Switch active policy via `SCORING_POLICY_VERSION` / `IDENTITY_POLICY_VERSION` env.

### UI

| File | Plain language |
|---|---|
| `app/templates/base.html` | Layout, nav (Summary/Leads/Manual Review/Dead Letter + JSON/Health), evaluation ID banner. |
| `app/templates/dashboard_summary.html` | KPI cards (total/valid/invalid/leads/pending/dead-letter), banner (`PASS/FAIL` + variance), by-source/by-decision tables, queues, reconciliation detail. Reuses real `reconciliation()` so badge is live, not hard-coded. |
| `app/templates/leads_list.html`, `lead_detail.html` | Filtered lead list + full trail (score features JSON, attribution, source event). |
| `app/templates/manual_review.html`, `dead_letter.html` | Queues with inline actions/forms, replay links. |
| `app/static/style.css` | One small stylesheet, system font, semantic tables, `badge-PASS/FAIL`, polished but no framework. |

### Fixtures & tests

| Path | Plain language |
|---|---|
| `fixtures/web_form_events.json`, `social_mention_events.json`, `email_engagement_events.json` | Synthetic, DAXVORA-shaped payloads — clean, duplicates, edits, ambiguous names, edge cases. |
| `fixtures/generate_and_post.py` | Posts fixtures to `POST /api/v1/events` (sync `requests`, so it works without app deps). `--dry-run` prints, default hits `http://localhost:8000`. Also polls reconciliation after seeding. |
| `tests/conftest.py` | **Pins** DB URL to isolated test DB (or macOS `/tmp` socket) **before** any app import, sets `ADMIN_API_KEY=test_admin_key`. Fixtures `db_engine` (drop/create per test), `db_session`, `client` (`httpx` ASGI). |
| `tests/unit/` | Fast, no DB: schema validation, `dedupe_key` hashing, fuzzy helper, scoring, routing rules, migration length, LLM provider selection. |
| `tests/integration/` | Real test Postgres: happy paths per source, duplicate/edit, identity flows, manual-review→resume, simulate-failure→dead-letter→replay, out-of-order attribution, reconciliation `variance==0`, concurrency races (duplicate/identity/manual-review), dashboard HTML. |
| `pytest.ini` | `asyncio_mode=auto`, `testpaths=tests`, `live` marker for paid live LLM test (`RUN_LIVE_INTERPRET_TEST=1` + real `OPENROUTER_API_KEY`). |

### Infra

| File | Plain language |
|---|---|
| `docker-compose.yml` | Local-only: `db` (postgres:15-alpine, `dsw` + `dsw_test` via `docker/init`), `app` (build `.`, waits for `db` healthy, env defaults for app + test DB). Ports `5432`, `8000`, volume `pgdata`. |
| `Dockerfile` + `docker-entrypoint.sh` | `python:3.11-slim`, `pip install -r requirements.txt`, copy source, `alembic upgrade head`, `uvicorn app.main:app --host 0.0.0.0 --port 8000`. |
| `.dockerignore` | Excludes `.env`, `.venv`, `__pycache__`, etc. |
| `alembic.ini` + `app/db/migrations/` | Migrations; DB URL injected at runtime. |
| `requirements.txt` | Pinned: `fastapi==0.141.1`, `uvicorn[standard]`, `sqlalchemy[asyncio]==2.0.52`, `asyncpg`, `alembic`, `pydantic==2.13.4`, `structlog`, `tenacity`, `httpx`, `openai==2.54.0`, `jinja2`, `pytest`/`pytest-asyncio`. |
| `scripts/clean_run.sh` | Drops `dsw_test`, re-creates, runs `pytest -q` with wall-clock, fails if ≥300s, checks `docker compose config`. |

---

## Setup for a new environment

You need **only Docker + Docker Compose**. Nothing else to install locally unless you want to run `pytest` natively (then Python 3.11+).

### 1) First time on a brand-new machine / fresh clone

```bash
# clone (or unpack the zip) and enter
git clone <your-fork-or-candidate-repo> demand-signal-workflow
cd demand-signal-workflow

# 1. environment file — you MUST set ADMIN_API_KEY (app fails fast without it)
cp .env.example .env
# open .env and set at least:
#   ADMIN_API_KEY=any-random-string-you-pick
# for live LLM also set ONE of:
#   LLM_PROVIDER=openrouter + OPENROUTER_API_KEY=sk-or-... + CLASSIFICATION_MODEL=deepseek/deepseek-v4-flash
#   LLM_PROVIDER=groq      + GROQ_API_KEY=gsk_...       + CLASSIFICATION_MODEL=openai/gpt-oss-20b
# Tip: on macOS you can generate one with: openssl rand -hex 16

# 2. build + start (app + Postgres)
docker compose build
docker compose up -d

# 3. confirm running (DB probe is real, not hard-coded)
curl http://localhost:8000/health
# → {"status":"ok","db":"ok"}   (if {"status":"degraded","db":"error"}, see Troubleshooting)

# 4. open the dashboard
open http://localhost:8000/dashboard
# or http://localhost:8000/api/v1/dashboard/reconciliation for JSON

# 5. seed sample data (so the dashboard is not empty)
pip install requests   # only for the seeder if not already installed
python fixtures/generate_and_post.py
# → "Seeded 28/28 events in 16.61s" + "Reconciliation: PASS variance=0" on the recommended Groq path
```

That is it — API, DB, and dashboard are live at `http://localhost:8000`.

### 2) Running without Docker (native Python, for tests)

```bash
# Python 3.11+ required
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# set env (or export inline) — ADMIN_API_KEY still required
cp .env.example .env
# edit .env: ADMIN_API_KEY=...

# local Postgres must be running and have dsw + dsw_test DBs
# macOS Homebrew default (used by tests): postgresql running with socket /tmp
# Linux: change TEST_DATABASE_URL in .env or tests/conftest.py to host=/var/run/postgresql
# then:
uvicorn app.main:app --reload --port 8000
```

### 3) Fresh environment checklist (copy/paste)

```bash
# from project-root:
ls -la .env 2>/dev/null || echo "MISSING .env — run cp .env.example .env and set ADMIN_API_KEY"
docker compose config >/dev/null && echo "compose config ok" || echo "compose config FAIL — check .env quoting"
docker compose up -d && docker compose ps
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/api/v1/dashboard/reconciliation | jq '.variance, .status'
# expect variance 0, status PASS after seeding
```

### 4) Stopping / resetting

```bash
docker compose down              # stop, keep data
docker compose down -v           # stop + wipe Postgres volume (dev + dsw_test are deleted)
docker compose up -d --build     # rebuild after code/pip changes
docker compose logs -f app       # tail app logs
docker compose logs -f db        # tail db logs
```

> **Evaluator note:** `docker compose down -v` is required the **first time** you set up, because the test database `dsw_test` is created only when the Postgres volume is first initialized (`docker/init`). If tests say they cannot find `dsw_test`, this is the fix.

---

## Configuration — environment variables

Everything via `.env` (git-ignored). See `.env.example` and `app/config.py` for the single source of truth.

| Variable | Required? | Default (Docker) | What it does |
|---|---|---|---|
| `ADMIN_API_KEY` | **Yes** | *(none — must set)* | Bearer token for `POST /api/v1/admin/*` (replay, simulate-failure). No fallback on purpose — app fails to start without it. |
| `DATABASE_URL` | No | `""` (empty) in code (`app/config.py: database_url: str = ""`); effective `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw` via `docker-compose.yml` `${DATABASE_URL:-...}` when `docker compose up` | App DB. Override for native runs (native default must be set, compose supplies one). |
| `TEST_DATABASE_URL` | No | `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw_test` via `docker-compose.yml` (isolated `dsw_test`); native fallback `rajatthakral@/dsw_test?host=/tmp` (macOS) or `host=/var/run/postgresql` (Linux) | Isolated DB for tests (never shares dev data). |
| `LLM_PROVIDER` | No | `openrouter` | `openrouter` (via `openrouter.ai/api/v1`) or `groq` (via `api.groq.com/openai/v1`). Both use OpenAI-compatible `chat.completions`. |
| `OPENROUTER_API_KEY` | If `LLM_PROVIDER=openrouter` | `""` | Needed when provider is OpenRouter. |
| `GROQ_API_KEY` | If `LLM_PROVIDER=groq` | `""` | Needed when provider is Groq. From `console.groq.com`. |
| `CLASSIFICATION_MODEL` | No | `deepseek/deepseek-v4-flash` | Pinned model ID. With Groq use `openai/gpt-oss-20b` (recommended after live testing). See `docs/Cost_and_Limits.md`. |
| `SCORING_POLICY_VERSION` | No | `scoring_policy_v1.json` | Basename under `app/policies/`. |
| `IDENTITY_POLICY_VERSION` | No | `identity_policy_v1.json` | Basename under `app/policies/`. |
| `RETRY_MAX_ATTEMPTS` | No | `3` | Bounded retries for LLM timeouts/429/5xx (FR-11). |
| `RETRY_BASE_DELAY_MS` | No | `500` | Base delay for exponential backoff + jitter. |
| `INTERPRET_MIN_TOKENS` | No | `2` | Free text < this many whitespace tokens → `unknown` **without** calling LLM. `hi`/`test` only; `want a quote` (3) does call. |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `APP_ENV` | No | `local` | `local` or `test` (tests force `test`). |

Empty values in `.env.example` (e.g. `DATABASE_URL=`) mean “use the default” — empty string is treated as missing so the `Field` default applies (see `app/config.py:_empty_str_to_none`).

---

## Running the app — health, seed, dashboard

```bash
# health (real DB probe)
curl http://localhost:8000/health
# open dashboard
open http://localhost:8000/dashboard
# live reconciliation (must be 0)
curl http://localhost:8000/api/v1/dashboard/reconciliation | jq
# summary counts
curl http://localhost:8000/api/v1/dashboard/summary | jq
```

**Seeding** posts a realistic mix (clean, duplicates, edits, ambiguous, edge cases) and prints reconciliation right away:

```bash
python fixtures/generate_and_post.py                       # to localhost:8000
python fixtures/generate_and_post.py --base-url http://localhost:8000
python fixtures/generate_and_post.py --dry-run             # print without POSTing
```

With the recommended `LLM_PROVIDER=groq` + `GROQ_API_KEY` + `CLASSIFICATION_MODEL=openai/gpt-oss-20b`, expect `28/28` in ~16s, `variance=0`, and a mix like `8 hot / 3 warm / …` (see `docs/evidence/reconciliation_live_groq_run.json`).

---

## Using the API

```bash
# ingest one event (web_form)
curl -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" -d '{
    "source":"web_form","external_event_id":"wf-001","schema_version":"1.0",
    "received_at":"2026-08-20T12:00:00Z","consent":true,
    "name":"Ada Lovelace","email":"ada@example.com","message":"Need pricing for a team of 20, enterprise tier with SSO."
  }' | jq

# duplicate (same external_event_id + same body) → {duplicate:true}, no new lead
# edit (same external_event_id, different body) → {is_edit:true}, re-runs pipeline

# list leads, filtered
curl "http://localhost:8000/api/v1/leads?decision=hot" | jq
curl "http://localhost:8000/api/v1/leads?source=web_form" | jq
curl http://localhost:8000/api/v1/leads/<lead_id> | jq

# manual review queue
curl "http://localhost:8000/api/v1/manual-review?status=pending" | jq
curl -X POST http://localhost:8000/api/v1/manual-review/<review_id>/resolve \
  -H "Content-Type: application/json" -d '{"decision":"create_new"}'
# or: -d '{"decision":"merge_into","identity_id":"<uuid>"}'

# dead letter (FR-11)
curl "http://localhost:8000/api/v1/dead-letter?resolved=false" | jq

# admin — replay & simulate-failure (Bearer required)
curl -X POST http://localhost:8000/api/v1/admin/replay/<event_id> \
  -H "Authorization: Bearer $ADMIN_API_KEY"
curl -X POST http://localhost:8000/api/v1/admin/simulate-failure \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"stage":"interpret","event_id":"<uuid>"}'

# reconciliation must always be 0
curl http://localhost:8000/api/v1/dashboard/reconciliation | jq '.variance, .status, .reconciliation'
```

PRD error shapes (flat envelope): `400 {"error":"malformed_json"}` for not-JSON, `200 {"is_valid":false,"invalid_reason":"..."}` for schema failures (still persisted), `200 {"duplicate":true}`, `202 {"status":"dead_letter","stage":"interpret"}`, `200 {"status":"manual_review","review_id":"..."}`.

---

## Running tests

### Option A — Docker (mirrors the evaluator's run)

```bash
# from project root — app image already has pytest
docker compose run --rm app pytest -q
# or: docker compose exec app pytest -q
```

### Option B — Native (fastest on a backed-up dev DB)

```bash
# the suite DROPS and RECREATES dsw_test — never the dev dsw
pytest -q                         # all tests (mocked LLM, no cost, offline)
pytest -k "not live" -q           # same (live test is opt-in)
RUN_LIVE_INTERPRET_TEST=1 pytest -k live -q   # one real LLM call (needs real OPENROUTER_API_KEY)

# the evaluator's clean-environment check (must finish <5 minutes)
bash scripts/clean_run.sh
# → drops dsw_test, recreates, runs pytest -q with timing, checks docker compose config
```

**What the tests show:** they assert every acceptance criterion — schema for all three sources, duplicate no-op + concurrency, edit keeps `variance=0`, fuzzy never auto-merges, `unknown` → `needs_review`, routing `rule_matched` + fallback, first-touch immutable / last-touch latest + out-of-order, every mutation has a receipt, `reconciliation.variance==0` on seeded data including duplicates and simulated failures, logs contain no raw PII.

---

## Dashboard guide

Four screens, all server-rendered Jinja2, no JS build:

- **`/dashboard`** — summary: KPI cards (total/valid/invalid/leads/pending/dead-letter), by-source and by-decision tables, queues with links, **reconciliation banner** (`PASS` green / `FAIL` red) and per-entity detail. Badge is live from `reconciliation()` — not hard-coded.
- **`/dashboard/leads`** — filtered lead list (`status/source/decision`), with `queue/rule_matched/score/decision/escalated/sla_deadline`. Click ID for full trail.
- **`/dashboard/leads/{id}`** — route + score features, `policy_version`, attribution, source event (including `is_edit` / `payload_hash`).
- **`/dashboard/manual-review`** — pending first, then resolved, with inline `Create new` / `Merge into` forms (HTML posts to same logic as the JSON API, no bearer for this evaluator tooling).
- **`/dashboard/dead-letter`** — outstanding vs resolved, error + retry count, `replay` link (POST with Bearer).

Every number traces to DB rows → receipts. Reconciliation also available as JSON at `GET /api/v1/dashboard/reconciliation` and `GET /api/v1/dashboard/reconciliation?since=…&until=…`.

---

## Cost & limits (LLM)

Measured, not estimated — see [`docs/Cost_and_Limits.md`](docs/Cost_and_Limits.md) for the full table:

- **Historical paid run** (DeepSeek V4 Flash via OpenRouter, 212 tokens): `$0.000026` — key now expired, kept as record.
- **Free-tier sweeps:** `nvidia/nemotron-3-ultra-550b-a55b:free` worked but `>30s` latency (misses PRD 3s target); `google/gemma-4-26b:free` hit OpenRouter shared free limit `20 req/min` (`429 free-models-per-min`); **`openai/gpt-oss-20b` via Groq free** succeeded **`28/28` in `16.61s`, zero dead-letters, `variance=0`** — **currently recommended**: `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `CLASSIFICATION_MODEL=openai/gpt-oss-20b`.
- **Savings built in:** text `<2` tokens (like `hi`) → `unknown` with no call; duplicates skip the call; provider retries are bounded (3, 500ms base + jitter).
- Mocked tests cost nothing; the single `live` mark is opt-in.

---

## Troubleshooting

- **App won't start — `ADMIN_API_KEY is required`:** set `ADMIN_API_KEY` in `.env` (no default on purpose). `cp .env.example .env` then edit.
- **A real event (text ≥2 tokens) returns `202 dead_letter` or `500`:** missing API key for the active provider. If `LLM_PROVIDER=openrouter` set `OPENROUTER_API_KEY`; if `groq` set `GROQ_API_KEY`. Short noise like `hi` deliberately skips the call and is expected `unknown`.
- **Tests say `could not translate host name "db"` or `dsw_test does not exist`:** `docker compose down -v` then `docker compose up -d` again — `dsw_test` is created only on first volume init (`docker/init`). For native Postgres: ensure it is running and `TEST_DATABASE_URL` socket path matches your OS (`/tmp` macOS Homebrew, `/var/run/postgresql` Linux).
- **Linux instead of macOS:** set `TEST_DATABASE_URL=postgresql+asyncpg://user@/dsw_test?host=/var/run/postgresql` or set `DATABASE_URL`/`TEST_DATABASE_URL` in `.env`.
- **Port 8000 in use:** `lsof -i :8000` then stop the holder, or change `ports: - "8001:8000"` in `docker-compose.yml` and seed with `--base-url http://localhost:8001`.
- **Rate limit `429 free-models-per-min`:** you hit OpenRouter free tier `20/min`. Switch to paid key, throttle, or use `LLM_PROVIDER=groq` (no shared free limit; 30 RPM / 1,000 RPD per Groq docs).
- **Slow or timeout with Nemotron:** known `>30s` single call on that model via OpenRouter — switch to `openai/gpt-oss-20b` via Groq for the fast path.
- **Reconciliation shows `FAIL` variance >0:** a transaction wrote an entity without its receipt — file a bug. No seeded fixture (duplicates/edits/failures) should ever cause this; variances are surfaced, not hidden.

---

## Known limitations

Upfront — not oversights, but v1 choices (full text in [`docs/Known_Limitations.md`](docs/Known_Limitations.md)):

- **Single server + single DB.** No horizontal scaling, no Kafka/SQS. `dead_letter_queue` is the in-DB queue.
- **No real connectors.** Fixtures only. No live webhook from a form/social/email provider.
- **No multi-tenant / RBAC.** One static admin key, one workspace.
- **No retention/deletion.** Events and receipts are kept forever — no GDPR delete yet.
- **Dashboard is evaluator tooling, not branded.** Polished for clarity (KPI cards, badges, plain CSS), but intentionally no marketing polish — that is a separate project.
- **Escalation on-read only.** `routes.escalated` flips on the next `GET /leads` read, not via a scheduler.

Two real bugs found during live seeding and fixed are in [`docs/Plan_vs_Built.md`](docs/Plan_vs_Built.md).

---

## More detail

- [`docs/PRD_Demand_Signal_Workflow_v1_2.md`](docs/PRD_Demand_Signal_Workflow_v1_2.md) — full requirements (FR-1 … FR-11, flows, schema, API table)
- [`docs/Project02_Implementation_Plan_v1.md`](docs/Project02_Implementation_Plan_v1.md) — phase-by-phase build plan
- [`docs/Cost_and_Limits.md`](docs/Cost_and_Limits.md) — measured LLM cost, rate limits, perf vs PRD §5
- [`docs/Plan_vs_Built.md`](docs/Plan_vs_Built.md) — what diverged from the plan and why
- [`docs/Known_Limitations.md`](docs/Known_Limitations.md) — honest gaps
- [`docs/Submission_Checklist.md`](docs/Submission_Checklist.md) — pre-send checks
- [`AI_USAGE.md`](AI_USAGE.md) + [`ai-usage.json`](ai-usage.json) — AI assistance disclosure

---

*Built to be explainable: one pipeline, one lead per real person, one receipt per mutation, and a dashboard that tells the truth when something does not add up.*

