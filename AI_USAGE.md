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

### Session: Phase 1 follow-up — Docker boot path, centralized error handler, Docker-based test run
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0003`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `~deepseek/deepseek-v4-flash-latest`
- **What was generated / changed:** `docker-entrypoint.sh` (runs `alembic upgrade
  head` then starts uvicorn) + `Dockerfile` uses it as `ENTRYPOINT` so a fresh
  container self-migrates on boot with no manual alembic; `app/errors.py`
  (`MalformedJSONError` typed exception) + centralized `MalformedJSONError`
  exception handler in `app/main.py`; `app/routers/events.py` now `raise`s the
  typed exception instead of returning an inline `JSONResponse`. Confirmed
  `email-validator==2.2.0` present in `requirements.txt` and installed.
- **Verification (Docker-based, not the local-Postgres bypass):** `docker compose
  down -v` (wiped the DB volume) → `docker compose build` → `docker compose up`
  succeeded; app log shows the entrypoint auto-ran `alembic upgrade head` →
  `create events table`, then uvicorn booted; `\d events` inside the `db` service
  confirms `events_dedupe_key_key UNIQUE CONSTRAINT`; `POST /api/v1/events`
  exercised live: valid create → `duplicate:true` on resubmit → malformed →
  `400 {"error":"malformed_json"}` → schema-invalid → `200 is_valid=false`
  (no manual alembic run anywhere). Full suite `pytest` re-run from inside the
  app image against the compose `db` service (tests mounted via volume since
  `.dockerignore` keeps `tests/` out of the image): **26 passed in 0.24s**.

---

### Session: Phase 1 follow-up — isolated test database
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0004`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `~deepseek/deepseek-v4-flash-latest`
- **What was generated / changed:** `docker/init/01-test-db.sql` (creates
  `dsw_test` on first volume init, alongside dev `dsw` on the same Postgres);
  `docker-compose.yml` mounts that init script and adds `TEST_DATABASE_URL`
  (defaulting to `...@db:5432/dsw_test`); `tests/conftest.py` now *hard-assigns*
  `DATABASE_URL` from `TEST_DATABASE_URL` before importing the app, so the pytest
  process (app-under-test + fixtures) pins to the isolated test DB; README gained
  a short setup/one-line-reason note.
- **Human review / changes:** a real defect was caught during verification — the
  first conftest change used `os.environ.setdefault("DATABASE_URL", ...)`, which
  inside the app container silently kept the existing dev `DATABASE_URL=dsw`
  (setdefault never overrides a present value), so the suite dropped `events` in
  dev `dsw`. Fixed to a hard assignment; the curl-before / test-run / curl-after
  sequence then passed.
- **Verification:** `docker compose down -v` → `up` created both `dsw` and
  `dsw_test` (`\l` lists both). POSTed a dev event into dev `dsw`, then ran the
  in-container suite (tests + pytest.ini mounted) pointed at `TEST_DATABASE_URL`:
  **26 passed**; afterwards GET of the dev event still returned **200** with its
  full body and dev `dsw` `SELECT count(*)=1` — the test run no longer wipes dev
  data. Re-ran the suite a final time: **26 passed in 0.23s**, dev event GET still
  200.

---

### Session: Phase 2 — Resolve: Identity Resolution & Manual Review
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0005`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `~deepseek/deepseek-v4-flash-latest`
- **What was generated:** `identity_policy_v1.json` (rule order, confidence
  threshold 0.85, fuzzy algorithm choice documented); `identities`,
  `identity_links`, `manual_review_queue` tables in `app/db/models.py` + Alembic
  migration `0002_identity_tables`; `app/services/resolve.py` (normalizers,
  `fuzzy_similarity` via difflib SequenceMatcher, `should_auto_link` boundary
  gate, `resolve_identity`, `get_pending_reviews`, `resolve_review`);
  `app/routers/manual_review.py` (GET list + POST resolve); wired resolution into
  the POST /api/v1/events pipeline (manual_review parks the event, pipeline
  halts); response schema gains status/review_id/identity_id.
- **Fuzzy algorithm choice (documented in the policy file):** deterministic
  stdlib `difflib.SequenceMatcher.ratio()` over lowercase token sequences of the
  display name — chosen over a third-party fuzzy library to keep the stack
  minimal and fully deterministic (scoring determinism is a PRD requirement) and
  because token-set ratio handles name-with-typo cases well at this assessment
  scale.
- **What is still a placeholder:** on resolution the pipeline "resumes" by linking
  the event to the chosen identity, but interpret/score/act do not exist yet
  (Phases 3–5); `review_resolved` receipt is Phase 6.
- **Human review / changes:** pending Rajat review.
- **Verification:** `pytest` against real test Postgres — **50 passed, 0 failed**.
  Exact email/phone auto-link (and same email reuses one identity); fuzzy
  above-threshold (identical "Ada Lovelace") auto-links to the same identity;
  fuzzy below-threshold (shares only "Ada") goes to manual review with a
  `fuzzy_name_match_below_threshold` reason and NO identity_link created; the
  "force auto-merge below threshold" invariant is asserted both via the pure
  `should_auto_link` decision gate and via a real resolution call that confirms no
  link is written; the 0.849 / 0.85 / 0.851 threshold boundary is unit-tested
  (0.849→no link, 0.85→link, 0.851→link). HTTP tests confirm
  GET /api/v1/manual-review?status=pending and POST .../resolve (merge_into /
  create_new) with 400/404/409 contracts. Alembic migration chain 0001→0002 ran
  clean on a fresh DB.

---