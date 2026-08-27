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

> **Connectors: SIMULATED. Classification: LIVE call via OpenRouter or Groq (selectable).**

| Component | Label | What it means | Where |
|---|---|---|---|
| `web_form` connector | **SIMULATED** | Internal fixture generator. No real webhook receiver, no third-party form. | `app/schemas/events.py`, `app/routers/events.py` |
| `social_mention` connector | **SIMULATED** | Internal fixture generator. **No Reddit/Twitter/Discord API is ever called.** | `app/schemas/events.py`, `app/routers/events.py` |
| `email_engagement` connector | **SIMULATED** | Internal fixture generator. No ESP/mailbox integration. | `app/schemas/events.py`, `app/routers/events.py` |
| Interpretation / classification | **LIVE** | One real HTTP call per event to the selected provider (`LLM_PROVIDER`=`openrouter` via OpenRouter `OPENROUTER_API_KEY` **or** `groq` via Groq `GROQ_API_KEY` — **both real, working, disclosed options**; OpenRouter historical `212` tokens `$0.000026`, Groq **now proven** `28/28` in `16.61s` `0` dead-letters `variance=0` — see `## Cost & Limits` and `## Plan vs. Built`), `temperature=0` (OpenRouter) / `1e-8` (Groq, see Groq quirk), `max_tokens=200`, response parsed. Not mocked. Groq key is separate — get at `console.groq.com`, distinct from `OPENROUTER_API_KEY`. | `app/services/interpret.py` (`_call_llm`, `_InterpretClient`) |
| Identity resolution, scoring, routing, attribution | Deterministic local logic | Versioned JSON policy files, no external service. | `app/services/{resolve,score,act}.py`, `app/policies/` |
| `POST /api/v1/admin/simulate-failure` | **TEST-HARNESS ONLY** | Dead-letters an event without a real provider call, so replay can be exercised. Bearer-token gated. | `app/routers/admin.py` |
| Test suite LLM calls | **MOCKED** | `interpret._call_llm` is monkeypatched so tests are deterministic and offline. | `tests/integration/*` |

Two efficiency short-circuits mean not every event triggers the LIVE call: text below `INTERPRET_MIN_TOKENS` (default 2 words — pure noise like "hi"/"test", not short-but-real intent like "want a quote") is classified deterministically as `unknown`, and a true duplicate (same `dedupe_key` + `payload_hash`) returns early without re-running the pipeline.

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
| `DATABASE_URL` | no | `""` in code (empty string); effective `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw` via `docker-compose.yml` `${DATABASE_URL:-...}` when running `docker compose up` | App DB |
| `TEST_DATABASE_URL` | no | `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw_test` | Isolated test DB, hard-assigned to `DATABASE_URL` in `tests/conftest.py` |
| `OPENROUTER_API_KEY` | no* | `""` | LIVE classification via OpenRouter when `LLM_PROVIDER=openrouter` (`_call_llm`). Empty → runtime `RuntimeError` if long text needs classification and provider is openrouter. Tests mock it. |
| `GROQ_API_KEY` | no* | `""` | LIVE classification via Groq when `LLM_PROVIDER=groq` (console.groq.com, distinct from `OPENROUTER_API_KEY`). Empty → runtime `RuntimeError` if provider is groq and long text needs classification. Tests mock it. |
| `LLM_PROVIDER` | no | `openrouter` | Selects LIVE provider: `openrouter` (via OpenRouter) or `groq` (via Groq direct). Both use OpenAI-compatible `chat.completions`; see `app/services/interpret.py` branching and Groq `1e-8` temperature quirk. |
| `CLASSIFICATION_MODEL` | no | `openai/gpt-oss-20b` (Groq, recommended; see Cost) — OpenRouter alternative `deepseek/deepseek-v4-flash` still works via `LLM_PROVIDER=openrouter` | Pinned model for the selected provider. PRD example was `anthropic/claude-haiku-4.5`. When `groq`, use Groq-shaped IDs like `llama-3.3-70b-versatile`/`openai/gpt-oss-20b` (not OpenRouter `meta-llama/...:free`). |
| `ADMIN_API_KEY` | **yes** | *(none, fail-fast)* | Bearer for `POST /api/v1/admin/*` (replay, simulate-failure). `Settings()` raises `ValidationError` if unset. |
| `SCORING_POLICY_VERSION` | no | `scoring_policy_v1.json` | File under `app/policies/` |
| `IDENTITY_POLICY_VERSION` | no | `identity_policy_v1.json` | File under `app/policies/` |
| `LOG_LEVEL` | no | `INFO` | `structlog` JSON-lines |
| `APP_ENV` | no | `local` | `local`/`test` |
| `RETRY_MAX_ATTEMPTS` | no | `3` | Bounded retry (FR-11) |
| `RETRY_BASE_DELAY_MS` | no | `500` | Base for `wait_random_exponential` |

`*` `OPENROUTER_API_KEY` / `GROQ_API_KEY` empty is allowed for local runs that only test short-text (`<2` tokens → `unknown` without LLM) or mocked tests. Any long-text event (`≥2` tokens) will `500` without the key for the *selected* provider — set it from `.env.example` (`OPENROUTER_API_KEY` or `GROQ_API_KEY` at `console.groq.com`).

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

**Real measured numbers — four models across two providers, all live (not estimates):**

* **DeepSeek V4 Flash via OpenRouter** — *paid, original choice, historical* — `212 tokens (132 prompt + 80 completion)` on `deepseek/deepseek-v4-flash` = **`$0.000026` total** (USD) at DeepSeek V4 Flash pricing `($0.089 / 1M prompt, $0.177 / 1M completion)`. Stored per-result in `interpretations.token_usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`) and `model_version`/`prompt_version`. **Note:** the API key used for this 212-token run has since expired and this path is no longer actively tested, but the measured result stands as historical record (Phase 3, `AI_USAGE.md: S0007`).
* **NVIDIA Nemotron 3 Ultra via OpenRouter** — *free tier* `nvidia/nemotron-3-ultra-550b-a55b:free` — real live test **succeeded functionally** (correct JSON `{"label": …}` classification confirmed, `interpretations` row written), but exhibited **highly variable latency, including individual calls exceeding 30 seconds** — a real, measured finding, not an estimate. During real fixture seeding the client hit **read-timeouts at both 10s and 30s** (`httpx ReadTimeout` / `openai.APIConnectionError`) — this would **not** meet PRD §5 latency target (`<3s ingest→act`) under real conditions. No cost (free tier), but latency makes it unsuitable as the recommended default.
* **Google Gemma 4 26B via OpenRouter** — *free tier* `google/gemma-4-26b-a4b-it:free` — real live test **hit OpenRouter's shared free-tier rate limit** (20 requests/minute) under normal fixture-seeding load (28 `POST /api/v1/events` in quick succession). The actual `429` response body was `{"error":{"message":"Rate limit exceeded: free-models-per-min","code":429},"limit":20,"remaining":0}` (OpenRouter free tier generally, not Gemma-specific — same limit applies to all `*:free` models). This is a **real, reproducible constraint** of OpenRouter's free tier, not an estimate, and would require throttling or paid tier for full loads.
* **openai/gpt-oss-20b via Groq direct** — *free tier, recommended going forward* — real live test **succeeded completely**: **`28/28` fixture events seeded in `16.61` seconds, `ZERO` dead-letters, real decision distribution `8 hot / 3 warm / 4 needs_review / remainder cold or duplicate/manual-review` (from `GET /api/v1/dashboard/summary` and `GET /api/v1/dashboard/reconciliation`), reconciliation `variance=0` throughout (`GET /api/v1/dashboard/reconciliation` `{"variance":0,"status":"PASS","overall_status":"ok"}` on every poll). See `docs/evidence/reconciliation_live_groq_run.json` for the actual pasted response. **Token usage:** the live database for this run has since been reset (as expected for isolated test runs; `Base.metadata.create_all` per test), so per-call `token_usage` from `SELECT model_version, token_usage FROM interpretations WHERE was_skipped=false` is **not available** — only the aggregate counts/timing from the terminal output (`16.61s`, `28/28`, `0` dead-letters, decision distribution) are retained, per this project's convention of never presenting an estimate as a measurement (see Phase 3 gate precedent in `AI_USAGE.md`). Do **not** estimate token counts for this run.
* **Groq public free-tier rate limits (stated, not independently verified beyond this 28-event test):** `30 RPM / 1,000 RPD / 8,000 TPM / 200,000 TPD` per Groq's own published limits at `console.groq.com` — cited as Groq's stated limit, not something this project independently verified beyond **not hitting it** during the `28`-event `16.61s` test (`~1.7` req/s avg, well under `30` RPM). Documented here for context only.

**Which is recommended:** **`openai/gpt-oss-20b` via Groq (`LLM_PROVIDER=groq`, `GROQ_API_KEY` from `console.groq.com`, `CLASSIFICATION_MODEL=openai/gpt-oss-20b`)** — it is the **only** one of the four that completed a full live `28/28` run with **zero failures at real speed** (`16.61s` avg `0.59s` per event, well under PRD §5 `3s`). **OpenRouter remains fully supported** as a working alternative (per `LLM_PROVIDER` config added in `S0036`) — set `LLM_PROVIDER=openrouter` and `OPENROUTER_API_KEY` + `CLASSIFICATION_MODEL=deepseek/deepseek-v4-flash` (or any OpenRouter-shaped ID) and the same code path runs with `temperature=0` and `HTTP-Referer`/`X-Title` headers. This is **not** “Groq replaced OpenRouter,” it is “both work, Groq is the currently recommended default based on real comparative testing” (DeepSeek key expired, Nemotron too slow, Gemma rate-limited at 20/min).

**Model & params:** `deepseek/deepseek-v4-flash` (OpenRouter, historical) and `openai/gpt-oss-20b` (Groq, current recommended) both pinned via `CLASSIFICATION_MODEL`; OpenRouter list confirmed `deepseek/deepseek-v4-flash`, Groq list `openai/gpt-oss-20b`; `temperature=0` (OpenRouter) / `1e-8` (Groq, see Groq quirk in `app/services/interpret.py:210`), `max_tokens=200–400` (tuned for JSON).

* **Efficiency savings:** text `<2` tokens never calls the LLM (`label=unknown`, `model_version=none`, `was_skipped=true`) — pure noise like "hi"/"test" only; short-but-real intent like "want a quote" (3 words) does call the LLM (at ~$0.000026/call for OpenRouter/DeepSeek, Groq cost not yet measured but also sub-cent free tier, recall beats the saving); true duplicate (`dedupe_key + payload_hash` match) returns early with no pipeline. So not every event incurs the LIVE call.

**Rate limits & assumptions (updated after live testing):**

* OpenRouter (all `*:free` models) — shared free tier **20 req/min** (`free-models-per-min`), as hit with Gemma (actual `429` body above). Paid tier removes this; DeepSeek free-tier/paid limits are provider-controlled and not committed here. Local tests are **mocked** (`interpret._call_llm` monkeypatched) so they incur no live calls and work offline. The one `live`-marked OpenRouter test is opt-in (`RUN_LIVE_INTERPRET_TEST=1` + real `OPENROUTER_API_KEY`), never run by default.
* Groq — `30 RPM / 1,000 RPD / 8,000 TPM / 200,000 TPD` per Groq's published docs (console.groq.com); **not independently verified beyond not hitting it** during the `28`-event `16.61s` test. Groq direct has no OpenRouter-style `free-models-per-min` shared limit — the 28-event burst succeeded where Gemma failed.
* No other paid service is used. No message broker, no paid CRM/ESP/social API. Postgres via Docker Compose is the only infra. If you need higher throughput, the in-DB `dead_letter_queue` is the v1 stand-in for a queue (PRD §2).
* Token usage per request is independent of dataset size; 500-event dashboard perf (see below) is DB aggregation, not LLM.

**Explicit statement (updated):** **One paid OpenRouter call was incurred historically** (`212` tokens `$0.000026` on DeepSeek, key now expired — historical record). **All four live runs in this final sweep used free tiers only (Nemotron/Gemma via OpenRouter free, gpt-oss-20b via Groq free) — no additional paid spend.** The only future spend risk is tokens for the interpretation stage (OpenRouter/DeepSeek paid or Groq free), kept sub-cent by the 2-token noise-only skip and cheap model. No live Groq cost is claimed because free tier was used and per-call token data is not available after DB reset.

**Performance (measured, PRD §5):** `single ingest→act (mocked LLM, under seeded load) 72.23 ms <3000 ms`; `summary 22.95 ms`, `reconciliation 19.75 ms`, `dashboard HTML 32.06 ms` for 500 events (all `<1000 ms`). See Phase 10 `test_phase10_sweep` prints. Live Groq run `28` events in `16.61s` (`0.59s` avg per event, including scoring/routing/attribution, not just LLM) also well under `3s` — Nemotron's `>30s` single calls would not meet this.

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

**Live multi-provider classification testing (post-submission-prep) — additive, not a rewrite:**

* **Why:** The original `OPENROUTER_API_KEY` for `deepseek/deepseek-v4-flash` (`212` tokens `$0.000026`, `AI_USAGE.md: S0007`) expired after Phase 3. Rajat had only a free-tier key available going forward (`S0036` Groq support was already additive), so the project re-exercised the LIVE path with free-tier models to find a currently working, recommended default.
* **Four models/providers tried, in order, real outcomes (summary; full detail in `## Cost & Limits`):**
  1. `deepseek/deepseek-v4-flash` via OpenRouter (paid) — `212` tokens `$0.000026` historical, key now expired, no longer actively tested but stands as measured record.
  2. `nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter — functionally correct JSON, but **highly variable latency, >30s single calls, client read-timeouts at `10s` and `30s`** during real fixture seeding → would not meet PRD §5 `<3s`.
  3. `google/gemma-4-26b-a4b-it:free` via OpenRouter — hit **OpenRouter free-tier 20 req/min** (`429` body `Rate limit exceeded: free-models-per-min`, `limit 20`) under normal `28`-event burst → reproducible free-tier constraint.
  4. `openai/gpt-oss-20b` via Groq direct — **only one to complete a full clean run with zero failures at real speed**: `28/28` events in `16.61s`, `0` dead-letters, `variance=0` (see `docs/evidence/`), decisions `8 hot / 3 warm / 4 needs_review` + remainder.
* **Final decision:** **`openai/gpt-oss-20b` via Groq** (`LLM_PROVIDER=groq`, `GROQ_API_KEY`, `CLASSIFICATION_MODEL=openai/gpt-oss-20b`) — chosen because it was the **only** one of the four to finish a full `28/28` live run with **zero dead-letters** and `16.61s` avg `0.59s` (well under `3s`), while Nemotron was too slow and Gemma was rate-limited. OpenRouter (`deepseek`) remains fully supported as an alternative (`LLM_PROVIDER=openrouter`).
* **Two real defects found *during* this live testing that were *not* caught by the automated test suite — evidence that live, real-environment testing matters beyond a passing suite:**
  1. `alembic_version` `varchar(32)` overflow — `0013` revision `34` chars → `StringDataRightTruncationError` on fresh `docker compose up` (`AI_USAGE.md: S0034`, `app/db/migrations/versions/0013_dlq_unresolved_unique.py:32`). Suite never caught it because `tests/conftest.py:42` builds schema via `Base.metadata.create_all`, bypassing `alembic_version` entirely.
  2. Edit-of-dead-lettered-event `500` — second `POST` of same `external_event_id` while provider still failing hit `interpret.py:342` `IntegrityError` suppression then `MissingGreenlet` on expired `event.id` in `events.py:154` → unhandled `500` not `202` (`AI_USAGE.md: S0035`). Suite never caught it because no test did `dead-letter → edit same event while still failing`; existing dead-letter tests used distinct IDs and replay via `admin.py` doesn't touch `event.id` after `rollback`.

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
* **`OPENROUTER_API_KEY` empty + long text → 500:** set a real key in `.env` or keep text `<2` tokens / mock in tests (`interpret._call_llm`).
* **`psql: database "dsw_test" does not exist` on host:** `psql -h /tmp -d postgres -c "CREATE DATABASE dsw_test OWNER rajatthakral;"`.
