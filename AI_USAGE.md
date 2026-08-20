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

## Standing process notes
- **`docker compose build` before `docker compose up` whenever a new Alembic
  migration is added.** The app image has its migration files baked in at build
  time, so the in-container entrypoint's `alembic upgrade head` cannot see
  revisions that postdate the last build. A stale image silently applies only the
  older revisions (or fails), as happened at Phase 2's fixup (0003). Make
  build-then-up a standing habit every time migrations change, so the self-migrate
  entrypoint stay correct.
- **Never hardcode a real API key value into any script, heredoc, or command.**
  Always read secrets from the environment (`os.environ["OPENROUTER_API_KEY"]`,
  loaded via `set -a && source .env && set +a`) — including in throwaway debug
  scripts. If a raw provider response needs to be inspected for debugging, write
  the debug script to read the key from env, never paste the literal value into
  code.

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

### Session: Phase 2 fixup — Concurrency-safe identity resolution
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0006`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `~deepseek/deepseek-v4-flash-latest`
- **What was generated / changed:** Alembic migration `0003_identity_uniqueness`
  adds two **partial unique indexes** on `identities.primary_email` and
  `identities.primary_phone` (`WHERE <col> IS NOT NULL`); matching `Index`
  declarations added to the `Identity` model (`__table_args__`); the
  `exact_email`/`exact_phone` branches of `resolve_identity` now route through a
  concurrency-safe `_link_via_exact` helper that catches the loser's
  `IntegrityError`, rolls back, re-SELECTs the winner's identity and links to it
  (mirroring `ingest.create_event` from Phase 1); new concurrency tests for both
  email and phone.
- **Defect found & fixed during verification:** after ``db.rollback()`` the
  `event` ORM object is expired; a lazy re-read of `event.id` raised
  `MissingGreenlet` in async SQLAlchemy. Fixed by capturing the already-stable
  `event_id` before the flush/rollback rather than reading it off the expired
  object.
- **Verification:** full suite **52 passed, 0 failed** against real test Postgres,
  including new `test_concurrent_email_resolution_creates_one_identity` and
  `test_concurrent_phone_resolution_creates_one_identity` (each fires two
  `resolve_identity` calls via `asyncio.gather` for the same email/phone and
  asserts exactly **one** Identity row with both events linked to it). Concurrency
  tests run 3× consecutively (no flakiness). Docker path re-verified: rebuilt the
  stale app image so its baked-in Alembic revisions include 0002/0003, restarted,
  entrypoint auto-applied `0001→0002→0003` to the compose dev DB with the partial
  unique indexes present; full in-container suite against the isolated `dsw_test`
  DB passed (52).

---

### Session: Phase 3 — Interpret: LIVE classification via OpenRouter (FR-4)
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0007`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash`
- **REAL PAID API CALL in this phase.** Written approval from Krishnam (via Rajat)
  covers LIVE API usage for the interpretation stage (PRD §9). Ran 4–5 live calls
  total (probes while tuning `max_tokens` + one canonical test run). Measured
  cost: primary test run **212 tokens (132 prompt + 80 completion) on
  deepseek/deepseek-v4-flash = $0.000026 total**.
  (USD) at DeepSeek V4 Flash pricing ($0.089/M prompt, $0.177/M completion).
  Total across all probe/live calls ≈ **$0.0003** — sub-cent. Full numbers live in
  `interpretations.token_usage` and this entry; usable for the README cost section.
- **Model deviation (deliberate, disclosed):** the PRD's example string was
  `anthropic/claude-haiku-4.5`. I switched to **`deepseek/deepseek-v4-flash` via
  OpenRouter** because it is materially cheaper for repeated test runs. FR-4 and
  `interpretations.model_version` are provider/model-agnostic by design, so no PRD
  change is required — only this disclosure. Updated the default in
  `docker-compose.yml`, `.env.example`'s comment, `app/config.py`, and local
  `.env`. Model identifier confirmed against OpenRouter's live model list
  (`deepseek/deepseek-v4-flash`).
- **What was generated:** `interpretations` table + Alembic `0004_interpretations`;
  `app/services/interpret.py` (async `openai` SDK pointed at OpenRouter base URL,
  `temperature=0`, `max_tokens=200`, `model_version`+`prompt_version` on every
  result, tenacity bounded retry, sub-min-length LLM-skip); wired interpretation
  into the POST /api/v1/events pipeline (Flow 1 step 4) with a surfaced
  `interpret_status=error` on provider failure (dead-letter is Phase 8);
  response schema extended with interpret fields; upsert-on-edit so an edited
  resubmission doesn't violate the event_id-unique constraint.
- **What is still a placeholder:** dead-letter integration is Phase 8; on provider
  failure today the response carries `interpret_status=error` (visible) rather
  than writing a `dead_letter_queue` row.
- **Human review / changes:** pending Rajat review.
- **Verification:** full suite **54 passed, 1 skipped** (the one `live`-marked
  test requires `RUN_LIVE_INTERPRET_TEST=1` + a real key, so it's gated off by
  default per cost discipline). Short-text test uses a spy on `_call_llm` and
  asserts it is **never invoked** (provably skipped, not coincidentally unknown).
  Provider-failure test spies on `_call_llm`, observes exactly `retry_max_attempts`
  (3) attempts with exponential-backoff log lines, asserts `InterpretError` is
  surfaced and **no fabricated `unknown` row** is written. Real LIVE test (run
  once, `RUN_LIVE_INTERPRET_TEST=1`): HTTP 200, returned
  `label=pricing_inquiry confidence=0.95`, recorded model
  `deepseek/deepseek-v4-flash`, `prompt_version=interpret_v1`, token_usage
  `{prompt:132, completion:80, total:212}`, cost = $0.000026 total. In-container suite
  (isolated dsw_test) also **54 passed, 1 skipped**; entrypoint auto-applied
  migration 0004 to the compose dev DB (\d interpretations confirmed).

---

### Session: Phase 3 fixup — real openai retry condition + API-key security
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0008`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new API call)
- **What was generated / changed:**
  - **Retry condition widened to real openai SDK exceptions.** `classify_event`
    previously retried only `(RuntimeError, TimeoutError)`, which matched the
    test's synthetic error but NOT the real `openai.APITimeoutError` /
    `openai.RateLimitError` / `openai.APIConnectionError` /
    `openai.InternalServerError` an actual OpenRouter outage or rate-limit would
    raise. Added `_is_retryable()` predicate: retries `InterpretError`
    (bad/truncated-JSON parse), `openai.APIConnectionError` (covers timeouts), and
    `openai.APIStatusError` when status is 429 or >=500. It does NOT retry
    `AuthenticationError` (401 bad key) or other 4xx — config problems fail fast.
  - **Key rotation (security).** Rajat rotated the OpenRouter API key during this
    phase. Standing rule added to "Standing process notes": never hardcode a real
    API key into any script/heredoc; always read from env.
  - Removed the unused `Interpretation` import from `app/routers/events.py`.
  - Cleaned the confusing cost phrasing to the unambiguous
    "212 tokens = $0.000026 total".
- **Verification:** full suite **56 passed, 1 skipped** (up from 54). New tests:
  provider-failure now mocks a REAL `openai.APITimeoutError` (3 bounded attempts,
  `InterpretError` surfaced, no fabricated `unknown` row); `test_rate_limit_429`
  (2 attempts); `test_auth_error_401_fails_fast_no_retry` (1 attempt). The
  predicate is verified independently: 429→retry, 500→retry, 401→no, 400→no.

---

### Session: Phase 4 — Score: Versioned Policy Application
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0009`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new real API
  call this phase — scoring is deterministic policy application; the LIVE
  interpretation stage is exercised only through the already-run Phase 3 test).
- **What was generated:**
  - `app/policies/scoring_policy_v1.json` populated with `policy_version`,
    `label_scores` (incl. `"unknown": null`), `source_bonus`, `consent_bonus`,
    `campaign_bonus`, `confidence_multiplier_enabled` (+ rationale note),
    `decision_thresholds` (hot 70 / warm 45 / cold 20), `tie_break_rule`,
    `insufficient_data_rule`.
  - `Score` model in `app/db/models.py` (table `scores`; score nullable; features
    NOT NULL; event_id NOT unique).
  - Alembic `0005_scores` (creates `scores` + non-unique `ix_scores_event_id`;
    chains from `0004_interpretations`).
  - `app/services/score.py`: `_load_policy()` (cached), `compute_score()`
    (structural unknown=>null guard first, then multiplier + bonuses + clamp +
    threshold decision), `score_event()` (upsert by event_id).
  - Wired scoring into `app/routers/events.py` POST flow and GET
    `/api/v1/events/{id}`; extended `EventIngestResponse` with
    `score` / `decision` / `score_id`.
- **What is still a placeholder:** `score_event` does NOT write a `receipts` row
  (receipts table is Phase 6/7; reconciliation is Phase 7).
- **Human review / changes:** pending Rajat review.
- **Verification:** `pytest tests/unit/test_scoring_policy.py
  tests/integration/test_score_event.py -v` → **8 passed**. Full suite →
  **64 passed, 1 skipped** (target 64+1; live test gated). In-container suite
  against isolated dsw_test → **64 passed, 1 skipped**. Migration applied cleanly
  to compose dev DB; `\d scores` shows `score` nullable + `ix_scores_event_id`.
  Live Docker walkthrough: short-text event → `label=unknown`, `score:null`,
  `decision:"needs_review"`, `score_features:{label:unknown, insufficient_data:True}`,
  and `GET /api/v1/events/{id}` returns `score`/`decision`/`score_features`/
  `policy_version`.

---

### Session: Phase 5 — Act: Lead Creation, Routing, SLA
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0010`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new real API
  call this phase — routing is deterministic policy application; the LIVE
  interpretation stage is exercised only through the already-run Phase 3 test).
- **What was generated:**
  - `app/policies/routing_rules_v1.json` — ordered rule table: hot_any
    (sales_urgent, 2h), warm_pricing (sales_priority, 8h), warm_any
    (sales_default, 24h), needs_review_any (manual_queue, 48h), cold_any
    (nurture, 72h); fallback → unassigned / fallback_no_rule / 72h.
  - `Lead` and `Route` models in `app/db/models.py` (leads.identity_id UNIQUE;
    routes.rule_matched NOT NULL on every route including fallback).
  - Alembic `0006_leads_routes` (chains from `0005_scores`; uq_leads_identity_id,
    ix_leads_status, ix_routes_lead_id).
  - `app/services/act.py`: `_load_routing_rules()` (cached), pure
    `apply_routing_rule()` (first-match-wins, fallback on no match),
    `create_or_update_lead()` (UNIQUE-anchor idempotency, IntegrityError →
    re-read winner), `route_lead()` (sla_deadline = now + sla_hours), `act()`
    (single commit: lead + route together).
  - Wired act into `app/routers/events.py` POST flow (raw UUID identity);
    extended `EventIngestResponse` with lead_id/lead_op/route_id/queue/
    rule_matched/sla_deadline.
  - New `app/routers/leads.py` mounted in `app/main.py`: GET /api/v1/leads
    (filters: status/source/decision) and GET /api/v1/leads/{id} (full detail;
    404 flat `{"error": "not_found"}`).
- **What is still a placeholder:** the receipts write is a TODO stub inside
  `act()` — Phase 7 adds the actual write inside the same commit block without
  restructuring the transaction. `escalated` is computed-on-read for v1 (no
  scheduler).
- **Human review / changes:** pending Rajat review.
- **Verification:** `pytest tests/unit/test_routing_rules.py
  tests/integration/test_act.py -v` → **11 passed**. Full suite →
  **75 passed, 1 skipped** (target 75+1). In-container suite against isolated
  dsw_test → **75 passed, 1 skipped**. Migration applied to compose dev DB;
  `\d leads` shows uq_leads_identity_id + ix_leads_status; `\d routes` shows
  rule_matched NOT NULL + ix_routes_lead_id. Live Docker walkthrough: warm event
  → queue=sales_default / rule_matched=warm_any; re-POST same email →
  same lead_id, lead_op=updated, DB count=1 (exactly-once); GET /api/v1/leads
  and /leads/{id} return queue/rule_matched/sla_deadline/score/decision/features.

---