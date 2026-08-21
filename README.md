# Demand-Signal Scoring, Routing & Attribution Workflow

Evaluation ID: DAXVORA-RAJAT-2026-08-A01
Status: in progress — see docs/Project02_Implementation_Plan_v1.md

## LIVE / SIMULATED labels

> **Connectors: SIMULATED. Classification: LIVE call via OpenRouter.**

Recorded here per the Phase 1 and Phase 3 compliance gates (labels also appear at
the point of use in code, not only in this file).

| Component | Label | What it means | Where |
| --- | --- | --- | --- |
| `web_form` connector | **SIMULATED** | Internal fixture generator. No real webhook receiver, no third-party form provider. | `app/schemas/events.py`, `app/routers/events.py` |
| `social_mention` connector | **SIMULATED** | Internal fixture generator. **No Reddit/Twitter/Discord API is ever called.** | `app/schemas/events.py`, `app/routers/events.py` |
| `email_engagement` connector | **SIMULATED** | Internal fixture generator. No ESP/mailbox integration. | `app/schemas/events.py`, `app/routers/events.py` |
| Interpretation / classification | **LIVE** | One real HTTP call per event to OpenRouter (`OPENROUTER_API_KEY`), real response parsed. Not mocked. | `app/services/interpret.py` (`_call_llm`) |
| Identity resolution, scoring, routing, attribution | Deterministic local logic | Versioned JSON policy files, no external service. | `app/services/{resolve,score,act}.py`, `app/policies/` |
| `POST /api/v1/admin/simulate-failure` | **TEST-HARNESS ONLY** | Dead-letters an event without a real provider call, so replay can be exercised. Bearer-token gated. | `app/routers/admin.py` |
| Test suite LLM calls | **MOCKED** | `interpret._call_llm` is monkeypatched so tests are deterministic and offline. | `tests/integration/*` |

Two efficiency short-circuits mean not every event triggers the LIVE call: text
below `INTERPRET_MIN_TOKENS` (default 8 words) is classified deterministically as
`unknown`, and a true duplicate (same `dedupe_key` + `payload_hash`) returns early
without re-running the pipeline.

> **Token/cost figures are not yet recorded.** The README cost section requires a
> measured number from a real OpenRouter run (Phase 3 gate), not an estimate; it
> is still outstanding.

## Setup (placeholder — full README in Phase 11)

- `cp .env.example .env`, fill in values, then `docker compose up`.
- On first boot the `db` service creates an isolated `dsw_test` database
  (`docker/init/01-test-db.sql`) alongside the dev database `dsw`.
  **Note:** the init script only runs when the Postgres data volume is first
  created. If you already have an existing volume (e.g. from before this script
  was added), run `docker compose down -v` once so the volume is recreated and
  `dsw_test` is created before running the test suite the first time.
- **Tests run against that isolated `dsw_test` database** (`TEST_DATABASE_URL`),
  so the suite's drop/create per test never touches the dev data the app operates
  on — manual walkthrough data and test data are kept apart by design.
  ```
  docker compose run --rm --no-deps \
    -v "$PWD/tests:/app/tests" -v "$PWD/pytest.ini:/app/pytest.ini" \
    --entrypoint pytest app -q
  ```

## Migrations

Schema changes are Alembic revisions under `app/db/migrations/versions/`. An
existing database needs `alembic upgrade head` after pulling — revision `0011`
replaces the global `UNIQUE(events.dedupe_key)` with a partial unique index scoped
to `is_valid = true`, so a rejected event no longer blocks either a repeat
rejection or a corrected resubmission of the same `external_event_id`.

