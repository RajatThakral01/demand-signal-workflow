# AI Usage Log

**Evaluation ID:** `DAXVORA-RAJAT-2026-08-A01`
**Project:** demand-signal-workflow

This document is the running, per-session log of AI-assisted work. It is updated
incrementally at the end of every phase (per Compliance Checklist) — never
reconstructed from memory at the end. The structured, machine-readable twin of
this file is `ai-usage.json`.

## How this is kept honest
- Every phase gets at least one session entry, written the moment the phase's
  verification is done.
- `generated_files` = files the model wrote; `human_modified_files` = what Rajat
  reviewed or changed afterward.
- `verification` records what was actually tested (real output, not assumption).
- No real secrets or raw PII are recorded here, and PII is masked if ever relevant.

## Sessions

### Session: Phase 0 — Scaffolding, Environment & Disclosure Setup
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0001`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `~deepseek/deepseek-v4-flash-latest`
- **What was generated:** repo skeleton per PRD §6; `docker-compose.yml`
  (FastAPI `app` + Postgres 15 `db`); `Dockerfile` + `.dockerignore`;
  `alembic.ini` + async `env.py` scaffold; `requirements.txt` (pinned);
  `.env.example` (names only); `app/config.py` (pydantic-settings),
  `app/logging.py` (structlog JSON-lines), `app/db/session.py` (+ `check_db()`),
  `app/db/models.py` (empty `Base`), `app/main.py` with live `GET /health`
  (real `SELECT 1`); package `__init__.py` files and `.gitkeep` placeholders;
  placeholder policy JSON files; `.gitignore`/`.dockerignore` updates; local
  (git-ignored) `.env`.
- **What is still a placeholder:** no feature logic; `openai` SDK pinned but
  unused; OpenRouter key empty (Phase 3); `scoring_policy_v1.json` /
  `identity_policy_v1.json` are `{}`; PII-redaction processor in `logging.py`
  is an intentional no-op until Phase 7; Alembic has no revision yet.
- **Human review / changes:** Rajat reviewed Phase 0 and requested three fixups,
  all applied in commit "Phase 0 fixup": (1) remove the hardcoded `ADMIN_API_KEY`
  default from `docker-compose.yml` and make `app/config.py` require it (verified:
  `Settings()` now raises `ValidationError` when unset, `/health` still 200 when
  it's set via `.env`); (2) add a `notes` field to `ai-usage.json` (top-level and
  per-session); (3) move the PRD and Implementation Plan into `docs/`.
- **Verification:** `docker compose up` from clean state succeeded and
  `http://localhost:8000/health` returned `{"status":"ok","db":"ok"}` (200)
  via a real `SELECT 1`. The degraded path was also exercised: stopping the DB
  made `/health` return `{"status":"degraded","db":"error"}` (503), and
  restoring it brought `/health` back to 200 — proving the check is live, not
  hardcoded. A structlog smoke-test inside the container confirmed a single
  JSON line emitting `input_id`/`decision`/`reason`/`action`/`result`/`error`
  /`timing_ms`. No secret-value concerns — `.env.example` is names only, real
  values live only in git-ignored `.env`.

---

### Session: Phase 1 — Ingest: Schema, Validation, Dedupe & Edit Detection
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0002`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `~deepseek/deepseek-v4-flash-latest`
- **What was generated:** `app/schemas/events.py` (discriminated-union schemas for
  `web_form` / `social_mention` / `email_engagement`, `schema_version`, `EventIn`
  Annotated union + `event_adapter` TypeAdapter); `app/schemas/responses.py`
  (`EventIngestResponse`); `events` table in `app/db/models.py` (UNIQUE on
  `dedupe_key`); Alembic migration `0001_events`; `app/services/ingest.py`
  (canonicalization + `compute_dedupe_key` / `compute_payload_hash`, invalid-event
  persistence, create/duplicate/edit logic with DB-constraint race handling);
  `app/routers/events.py` (POST/GET `/api/v1/events`); `get_db_session` dependency
  in `app/db/session.py`; mounting in `app/main.py`; `pytest.ini`; unit +
  integration tests; `email-validator` added to requirements.
- **What is still a placeholder:** `event_edited` receipt is a TODO (receipts
  table is Phase 6 — edit detected + row updated now); no pipeline beyond ingest
  (resolve/interpret/score/act are later phases).
- **Human review / changes:** pending Rajat review this session.
- **Verification:** `pytest` against a real test Postgres (`dsw_test` on the local
  socket) — **26 passed, 0 failed**. Malformed JSON→400 not persisted; valid-JSON-
  fails-schema→200 `is_valid=false` persisted; exact duplicate→no-op with
  `duplicate:true`, one events row; edit→`is_edit:true` row updated, no second
  row; concurrency test (`asyncio.gather` of two simultaneous create_event calls)
  yields exactly one row with statuses `["created","duplicate"]`; a direct-insert
  test proves the DB UNIQUE constraint (raw INSERT of a second row with the same
  `dedupe_key` raises `IntegrityError`) is what enforces dedupe, independent of
  app logic. Alembic `upgrade head` ran cleanly and `\d events` confirms
  `events_dedupe_key_key UNIQUE CONSTRAINT`.

---

*Model/provider note: model identifier is `~deepseek/deepseek-v4-flash-latest`
served via OpenRouter. Exact pinned model string recorded in each session entry.*