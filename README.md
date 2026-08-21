# Demand-Signal Scoring, Routing & Attribution Workflow

**Evaluation ID:** `DAXVORA-RAJAT-2026-08-A01`
**Status:** Phase 11 — documentation finalized, Phase 12 pending
**Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 + asyncpg, Alembic, Postgres 15, Structlog, Tenacity, OpenAI SDK (via OpenRouter), Jinja2, Pytest

## Purpose

Founder-led businesses receive demand signals from disconnected sources — web forms, community/social mentions, email/campaign engagement — with no unifying system. The same person becomes multiple contacts, scoring is inconsistent, leads duplicate on retries, and attribution is unverifiable.

This project is a **source-agnostic backend workflow** that turns raw, possibly-duplicated, possibly-incomplete signals into:

* deterministic, auditable, **exactly-once lead records** per canonical identity,
* reproducible `score/decision` from a versioned policy file,
* traceable `route (queue/rule_matched/SLA)` from an ordered rule table,
* immutable `first-touch` + recency-tracked `last-touch` attribution surviving out-of-order delivery and edits,
* and a **reconciliation proof** (`GET /api/v1/dashboard/reconciliation` `variance:0`) that nothing was lost or double-counted.

Primary user is the **DAXVORA evaluator** (runs seeded pack, inspects logs/receipts, replays duplicates/failures). Secondary persona is a 2–20 person founder who wants one trusted dashboard number — the dashboard here is evaluator tooling, not a branded frontend (Project 03 owns that).

See `docs/PRD_Demand_Signal_Workflow_v1_2.md` and `docs/Project02_Implementation_Plan_v1.md`.

## LIVE / SIMULATED labels

> **Connectors: SIMULATED. Classification: LIVE call via OpenRouter.**

| Component | Label | What it means | Where |
|---|---|---|---|
| `web_form` connector | **SIMULATED** | Internal fixture generator. No real webhook receiver, no third-party form. | `app/schemas/events.py`, `app/routers/events.py` |
| `social_mention` connector | **SIMULATED** | Internal fixture generator. **No Reddit/Twitter/Discord API is ever called.** | `app/schemas/events.py`, `app/routers/events.py` |
| `email_engagement` connector | **SIMULATED** | Internal fixture generator. No ESP/mailbox integration. | `app/schemas/events.py`, `app/routers/events.py` |
| Interpretation / classification | **LIVE** | One real HTTP call per event to OpenRouter (`OPENROUTER_API_KEY`), `temperature=0`, `max_tokens=200`, response parsed. Not mocked. | `app/services/interpret.py` (`_call_llm`) |
| Identity resolution, scoring, routing, attribution | Deterministic local logic | Versioned JSON policy files, no external service. | `app/services/{resolve,score,act}.py`, `app/policies/` |
| `POST /api/v1/admin/simulate-failure` | **TEST-HARNESS ONLY** | Dead-letters an event without a real provider call, so replay can be exercised. Bearer-token gated. | `app/routers/admin.py` |
| Test suite LLM calls | **MOCKED** | `interpret._call_llm` is monkeypatched so tests are deterministic and offline. | `tests/integration/*` |

Two efficiency short-circuits mean not every event triggers the LIVE call: text below `INTERPRET_MIN_TOKENS` (default 8 words) is classified deterministically as `unknown`, and a true duplicate (same `dedupe_key` + `payload_hash`) returns early without re-running the pipeline.

## Architecture

```mermaid
flowchart TD
    subgraph Inputs[Inputs — SIMULATED]
        WF[web_form fixture]
        SM[social_mention fixture]
        EM[email_engagement fixture]
        WF & SM & EM --> API[POST /api/v1/events]
    end

    subgraph Decisions[Decisions — versioned policy]
        IDP[identity_policy_v1.json<br/>exact email/phone → auto-link<br/>fuzzy → manual_review]
        SCP[scoring_policy_v1.json<br/>label→score + bonuses<br/>thresholds hot 70/warm 45/cold 20]
        RRP[routing_rules_v1.json<br/>ordered rules → queue/SLA<br/>fallback unassigned]
    end

    subgraph Agents[Agents / Automations]
        RESOLVE[resolve_identity<br/>normalize email/phone<br/>fuzzy difflib]
        INTERPRET[interpret classify_event<br/>LIVE OpenRouter<br/>min-length skip / retry]
        SCORE[score_event<br/>compute_score]
        ACT[act<br/>create_or_update_lead<br/>route_lead<br/>upsert_attribution]
        PIPE[run_downstream<br/>interpret→score→act shared]
        ESC[evaluate_escalation<br/>on-read SLA breach]
    end

    subgraph Storage[Storage — Postgres 15]
        EV[(events<br/>partial UNIQUE dedupe_key WHERE is_valid)]
        ID[(identities<br/>partial UNIQUE email/phone)]
        LK[(identity_links<br/>UNIQUE event_id)]
        MR[(manual_review_queue<br/>UNIQUE event_id)]
        IP[(interpretations<br/>UNIQUE event_id)]
        SC[(scores<br/>UNIQUE event_id)]
        LD[(leads<br/>UNIQUE identity_id)]
        RT[(routes<br/>UNIQUE lead_id)]
        AT[(attribution_touches<br/>UNIQUE identity_id)]
        RC[(receipts)]
        DL[(dead_letter_queue)]
    end

    subgraph Actions[Actions — API + Dashboard]
        LEAD[POST /events → 200/202<br/>GET /events/{id}]
        LEADS[GET /leads /l/{id}]
        MRQ[GET /manual-review<br/>POST /{id}/resolve]
        DASH[GET /dashboard<br/>summary + reconciliation badge<br/>HTML Jinja2]
        DLIST[GET /dead-letter<br/>replay_url]
    end

    subgraph Failures[Failure Paths]
        RETRY[tenacity retry<br/>max 3, 500ms exp + jitter<br/>429/5xx/timeout]
        DLQ[dead_letter_queue<br/>stage=interpret<br/>retry_count]
        REPLAY[POST /admin/replay/{id}<br/>Bearer $ADMIN_API_KEY<br/>idempotent]
        SIM[POST /admin/simulate-failure<br/>test harness]
        RECON[GET /dashboard/reconciliation<br/>variance PASS/FAIL]
    end

    API --> EV
    EV --> RESOLVE
    RESOLVE --> IDP
    RESOLVE --> ID & LK
    RESOLVE -->|pending| MR
    MR -->|resolve| PIPE
    PIPE --> INTERPRET
    INTERPRET --> RETRY --> DLQ
    DLQ --> REPLAY --> PIPE
    SIM --> DLQ
    PIPE --> SCORE --> SCP --> SC
    SCORE --> ACT --> RRP
    ACT --> LD & RT & AT & RC
    ESC --> RT
    RECON --> RC
    LEAD --> DASH
    DASH --> RECON
    DLIST --> DLQ
```

*Inputs* are fixture POSTs to the app's own API; *decisions* are file-based policies (no ML service); *agents* are service modules (not a broker); *storage* is Postgres with DB-level unique constraints as idempotency anchors; *actions* are JSON + Jinja2 HTML evaluators; *failure paths* are bounded retry → dead-letter → replay + reconciliation proof. Evaluator flow: seed → duplicate/edit → ambiguous → failure → replay → dashboard `variance:0`.

## Setup

```bash
cp .env.example .env   # fill OPENROUTER_API_KEY and ADMIN_API_KEY (required, no fallback)
docker compose build   # **must** rebuild when a new Alembic migration was added (image bakes migrations)
docker compose up -d
# wait for `db` healthy (2s interval, 15 retries)
alembic upgrade head   # entrypoint also runs this; manual only for existing DBs
```

On first boot `db` creates isolated `dsw_test` alongside dev `dsw` (`docker/init/01-test-db.sql`). **Volume caveat:** init runs only when the Postgres volume is first created. If you already had a volume before this file existed, run `docker compose down -v` once so `dsw_test` is created before the first test run.

**Tests run against isolated `dsw_test`** (`TEST_DATABASE_URL`), so `Base.metadata.drop_all/create_all` per test never touches dev data:

```bash
# Host (local Postgres on /tmp socket)
DATABASE_URL=postgresql+asyncpg://rajatthakral@/dsw_test?host=/tmp pytest -q

# Docker (tests mounted, not baked into image)
docker compose run --rm --no-deps \
  -v "$PWD/tests:/app/tests" -v "$PWD/pytest.ini:/app/pytest.ini" \
  --entrypoint pytest app -q

# Clean-environment timed run (<5m, PRD §1-4)
bash scripts/clean_run.sh   # DROP/CREATE dsw_test + time pytest -q (28s) + docker compose config check

# Seed the synthetic pack (SIMULATED) against a running API
python fixtures/generate_and_post.py --base-url http://localhost:8000
python fixtures/generate_and_post.py --dry-run   # prints what would be posted
```

Health: `GET /health` → `{"status":"ok","db":"ok"}` 200 (real `SELECT 1`), degraded `503` when DB down.

Dashboard: `GET /dashboard` (HTML), `GET /api/v1/dashboard/summary` + `/reconciliation` (JSON), `GET /dashboard/leads`, `GET /dashboard/manual-review`, `GET /dashboard/dead-letter`, `GET /static/style.css`.

## Configuration / Env Vars

All via `app/config.py` (`pydantic-settings`, reads `.env`, `extra=ignore`):

| Var | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | no | `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw` (compose) | App DB |
| `TEST_DATABASE_URL` | no | `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw_test` | Isolated test DB, hard-assigned to `DATABASE_URL` in `tests/conftest.py` |
| `OPENROUTER_API_KEY` | no* | `""` | LIVE classification via OpenRouter (`_call_llm`). Empty → runtime `RuntimeError` if long text needs classification. Tests mock it. |
| `CLASSIFICATION_MODEL` | no | `deepseek/deepseek-v4-flash` | Pinned model via OpenRouter (see Cost). PRD example was `anthropic/claude-haiku-4.5`. |
| `ADMIN_API_KEY` | **yes** | *(none, fail-fast)* | Bearer for `POST /api/v1/admin/*` (replay, simulate-failure). `Settings()` raises `ValidationError` if unset. |
| `SCORING_POLICY_VERSION` | no | `scoring_policy_v1.json` | File under `app/policies/` |
| `IDENTITY_POLICY_VERSION` | no | `identity_policy_v1.json` | File under `app/policies/` |
| `LOG_LEVEL` | no | `INFO` | `structlog` JSON-lines |
| `APP_ENV` | no | `local` | `local`/`test` |
| `RETRY_MAX_ATTEMPTS` | no | `3` | Bounded retry (FR-11) |
| `RETRY_BASE_DELAY_MS` | no | `500` | Base for `wait_random_exponential` |

`*` `OPENROUTER_API_KEY` empty is allowed for local runs that only test short-text (`<8` tokens → `unknown` without LLM) or mocked tests. Any long-text event (`≥8` tokens) will 500 without a key — set it from `.env.example`.

## Usage

```bash
# Create a lead (any source)
curl -X POST http://localhost:8000/api/v1/events -H "Content-Type: application/json" -d @fixtures/web_form_events.json | head

# Or seed all fixtures
python fixtures/generate_and_post.py

# List leads / filters
curl http://localhost:8000/api/v1/leads | jq
curl "http://localhost:8000/api/v1/leads?status=routed&decision=hot" | jq
curl http://localhost:8000/api/v1/leads/<lead_id> | jq   # score/features, route/rule_matched/SLA, attribution

# Manual review
curl http://localhost:8000/api/v1/manual-review?status=pending | jq
curl -X POST http://localhost:8000/api/v1/manual-review/<review_id>/resolve -H "Content-Type: application/json" -d '{"decision":"create_new"}' | jq
curl -X POST http://localhost:8000/api/v1/manual-review/<review_id>/resolve -H "Content-Type: application/json" -d '{"decision":"merge_into","identity_id":"<uuid>"}' | jq

# Dashboard & reconciliation
curl http://localhost:8000/api/v1/dashboard/summary | jq
curl http://localhost:8000/api/v1/dashboard/reconciliation | jq   # variance must be 0

# Dead letter + replay (admin)
curl http://localhost:8000/api/v1/dead-letter | jq
curl -X POST http://localhost:8000/api/v1/admin/simulate-failure -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" -d '{"stage":"interpret","event_id":"<event_id>"}' | jq
curl -X POST http://localhost:8000/api/v1/admin/replay/<event_id> -H "Authorization: Bearer $ADMIN_API_KEY" | jq
```

HTML: `open http://localhost:8000/dashboard` — summary with reconciliation `PASS/FAIL` badge (live, not hardcoded), leads, manual-review queue (forms), dead-letter list (with `replay_url`).

## Cost & Limits

**Real measured numbers (Phase 3 live run, not an estimate):**

* **Primary test run:** `212 tokens (132 prompt + 80 completion)` on `deepseek/deepseek-v4-flash` via OpenRouter = **`$0.000026` total** (USD) at DeepSeek V4 Flash pricing `($0.089 / 1M prompt, $0.177 / 1M completion)`. Stored per-result in `interpretations.token_usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`) and `model_version`/`prompt_version`.
* **Total across 4–5 probe + live calls during tuning:** `≈ $0.0003` — sub-cent.
* **Model:** `deepseek/deepseek-v4-flash` (pinned `CLASSIFICATION_MODEL`). OpenRouter model list confirmed `deepseek/deepseek-v4-flash` exists. `temperature=0`, `max_tokens=200` (tuned: smaller truncated reasoning + JSON).
* **Efficiency savings:** text `<8` tokens never calls the LLM (`label=unknown`, `model_version=none`, `was_skipped=true`); true duplicate (`dedupe_key + payload_hash` match) returns early with no pipeline. So not every event incurs the LIVE call.

**Rate limits & assumptions:**

* OpenRouter routes to DeepSeek; DeepSeek free-tier/rate limits are provider-controlled and not committed here. Local tests are **mocked** (`interpret._call_llm` monkeypatched) so they incur no live calls and work offline. Only one `live`-marked test (`RUN_LIVE_INTERPRET_TEST=1` + real `OPENROUTER_API_KEY`) makes a real call.
* No other paid service is used. No message broker, no paid CRM/ESP/social API. Postgres via Docker Compose is the only infra. If you need higher throughput, the in-DB `dead_letter_queue` is the v1 stand-in for a queue (PRD §2).
* Token usage per request is independent of dataset size; 500-event dashboard perf (see below) is DB aggregation, not LLM.

**Explicit statement:** **No paid service beyond the approved OpenRouter usage was incurred.** The only real spend risk is OpenRouter tokens for the interpretation stage, kept sub-cent by the 8-token skip and cheap model.

**Performance (measured, PRD §5):** `single ingest→act (mocked LLM, under seeded load) 72.23 ms <3000 ms`; `summary 22.95 ms`, `reconciliation 19.75 ms`, `dashboard HTML 32.06 ms` for 500 events (all `<1000 ms`). See Phase 10 `test_phase10_sweep` prints.

## Plan vs. Built

**Deliberate deviation — OpenRouter model switch:**

* **PRD example:** `anthropic/claude-haiku-4.5` via OpenRouter.
* **Built:** `deepseek/deepseek-v4-flash` via OpenRouter (pinned `CLASSIFICATION_MODEL`).
* **Why:** materially cheaper for repeated test runs (`$0.089` vs higher Haiku pricing), same OpenAI-compatible `chat.completions` shape via `openai` SDK. `FR-4` and `interpretations.model_version` are provider/model-agnostic by design, so no PRD change — only disclosure here, in `.env.example` comment, `docker-compose.yml`, `app/config.py`, and `AI_USAGE.md` Phase 3. Cost numbers above are for the built model; swap is a one-line env change.

**Other plan-vs-built notes:**

* **`fuzzy_name_company` now always manual_review:** Impl Plan §12 left "scheduler vs on-read" open; built chooses `on-read` (no scheduler, `app/services/escalation.py` `is_sla_breached` strict `<`, persisted once + `escalated` receipt). PRD's own recommendation (§12 line 421). Fuzzy auto-link disabled entirely — every fuzzy candidate is `review_queued` regardless of score (policy `auto_link:false`), per assessment "never auto-merge below threshold" plus the stronger post-0012 invariant that no fuzzy auto-links at all. Tests updated `test_resolve_helpers` to assert `should_auto_link` always `false`.
* **`identities.primary_company` added (migration `0012`):** stores `company` for `fuzzy_name_company` averaging `difflib.SequenceMatcher` over name *and* company when both known.
* **`scores.event_id` made `UNIQUE` + PG `ON CONFLICT` upsert (migration `0012`):** Phase 4 left `event_id` non-unique with app-level upsert; `0012` makes the DB the final authority so concurrent replay/resolve cannot manufacture duplicate scores.
* **`identity_links.event_id` + `manual_review_queue.event_id` `UNIQUE` (migration `0012`):** one link / one work item per event; `resolve_review` now atomic `UPDATE ... WHERE status='pending' RETURNING` so concurrent `POST /manual-review/{id}/resolve` yields `200` + `409`, not `200`+`200` (fixed after PRD edge-case suite exposed `[200,200]`).
* **Routes `escalated` evaluated on-read** (`app/services/escalation.py` + `GET /leads` commit) — matches PRD §12 open item, writes one `escalated` receipt per breach.
* **`events.dedupe_key` partial UNIQUE `WHERE is_valid=true` (migration `0011`):** replaces global UNIQUE so a rejected event no longer blocks repeat rejections or a corrected resubmission (the PRD `never silently drop an invalid event` fix).
* **Dashboard `summary` + HTML (`GET /dashboard`, `GET /dashboard/leads` etc.)** were Phase 9 per plan and are now present (see Architecture). No JS framework, one `style.css`.

**Known gaps — what remains / what you'd do with more time:**

* **Live cost in README** was outstanding until Phase 3; now recorded above (real `212` tokens). Future: re-run with a larger fixture batch to publish a 50-event average cost.
* **No scheduler for `escalated`:** on-read is correct per PRD, but a background job would make `GET /dead-letter` and dashboards eventually consistent without a read triggering a write. Would add `APScheduler` or `pg_cron` + a `last_escalation_check` watermark.
* **No `tenant_id`:** single-evaluator local use (`PRD §2` future consideration). Adding it would index `tenant_id` on every table and scope dedupe/policy per tenant.
* **Fixtures are synthetic `@example.com`:** no real Reddit/ESP data (assessment rule). Swapping to real connectors would keep the same `EventIn` discriminated union and add `tenant_id` + real webhook receivers behind `SIMULATED`→`LIVE` flip.
* **No retention/TTL:** events/receipts retained indefinitely per PRD §7 v1 limitation — explicit, not oversight. Would add `retention_days` per tenant + archival job.
* **Rate-limit handling is bounded retry, not circuit breaker:** would add circuit breaker + DLQ metrics/alerts if OpenRouter 429s spike.
* **`TEST_DATABASE_URL` host default `postgresql+asyncpg://rajatthakral@/dsw_test?host=/tmp`** assumes Homebrew Postgres on `/tmp` socket (darwin). Documented, but a Linux host would need `--host /var/run/postgresql`.

## Known Limitations

Be honest — real gaps, not omitted:

* **Single-process, single-DB:** no horizontal scaling, no message broker (intentional per PRD §2). `dead_letter_queue` is in-DB, not Kafka/SQS. Throughput is limited to one Postgres instance.
* **No real connectors:** all three sources are fixture POSTs to `POST /api/v1/events`. No live Typeform/Reddit/ESP webhook is in scope (PRD §2 out-of-scope, labeled SIMULATED everywhere).
* **No multi-tenant / RBAC:** single static `ADMIN_API_KEY`, single evaluator, no per-tenant isolation. `tenant_id` would be needed for production.
* **No retention/deletion:** events and receipts are append-only, never deleted (PRD §7). GDPR-style deletion or TTL is not implemented.
* **Dashboard is evaluator tooling, not polished:** plain semantic HTML, one `style.css`, system font, no mobile/responsive polish, no asset branding (intentional per PRD §5/§8 — Project 03 is the branded surface).
* **Escalation on-read only:** `routes.escalated` flips only when a lead is read (`GET /leads`), not via a background scheduler. PRD §12 recommends this for v1, but it means a breached SLA is invisible until the next read.
* **Cost is single-model, single-run:** `$0.000026` is one canonical 212-token call; burst pricing or a different model (e.g. Haiku) would differ. No free-tier SLA is guaranteed by OpenRouter/DeepSeek.
* **Host assumption:** `TEST_DATABASE_URL` default assumes macOS Homebrew Postgres socket `/tmp`. Linux needs `/var/run/postgresql`.

## Asset Provenance

**No external icon, font, or dataset beyond the project's own fixtures was used.**

* All event data in `fixtures/{web_form,social_mention,email_engagement}_events.json` is synthetic, clearly fake (`@example.com`, fake names/companies/handles), generated for this assessment. No client/private data (assessment rule).
* No external icon set, web font, or CSS framework was introduced. `app/static/style.css` is a single hand-written stylesheet (system font stack); templates use plain semantic HTML (`<table>`, `<nav>`, headings) with no JS framework or build step (PRD §6 Frontend: "no JS framework, no build step").
* No external dataset, model weights, or third-party fuzzy library (stdlib `difflib` only).

If any asset were added later, it would be listed here with its source and license.

## Migrations

Schema changes are Alembic revisions under `app/db/migrations/versions/`. An existing database needs `alembic upgrade head` after pulling — revision `0011` replaces the global `UNIQUE(events.dedupe_key)` with a partial unique index scoped to `is_valid = true`, so a rejected event no longer blocks either a repeat rejection or a corrected resubmission of the same `external_event_id`. `0012` adds `primary_company` and makes `identity_links.event_id`, `manual_review_queue.event_id`, `scores.event_id` UNIQUE (with dedupe of legacy duplicates).

## Troubleshooting

* **`ADMIN_API_KEY` validation error on start:** `ADMIN_API_KEY` is required with no default (only auth gate, fail-fast). `cp .env.example .env` and set it; `docker compose up` will not silently accept a hardcoded value.
* **DB volume already exists before `dsw_test` init:** `docker compose down -v` once, then `up` (see Setup volume caveat).
* **Image stale after new migration:** `docker compose build` then `up` (migrations are baked into image, entrypoint runs `alembic upgrade head`).
* **Tests wipe dev data:** they don't — `TEST_DATABASE_URL` (`dsw_test`) is isolated; `DATABASE_URL` (`dsw`) is unaffected (hard-assign in `tests/conftest.py`, not `setdefault`).
* **`OPENROUTER_API_KEY` empty + long text → 500:** set a real key in `.env` or keep text `<8` tokens / mock in tests (`interpret._call_llm`).
* **`psql: database "dsw_test" does not exist` on host:** `psql -h /tmp -d postgres -c "CREATE DATABASE dsw_test OWNER rajatthakral;"`.
