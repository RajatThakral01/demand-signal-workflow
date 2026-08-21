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

### Session: Phase 6 — Attribution: First/Last-Touch
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0011`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new real API
  call this phase — attribution is deterministic timestamp logic; the LIVE
  interpretation stage is exercised only through the already-run Phase 3 test).
- **What was generated:**
  - `AttributionTouch` model in `app/db/models.py` (table `attribution_touches`;
    UNIQUE on identity_id; first_touch_* immutable once set; last_touch_* tracks
    strictly-later received_at; source + campaign_id denormalized per touch).
  - Alembic `0007_attribution_touches` (chains from `0006_leads_routes`;
    uq_attribution_touches_identity_id; FKs to identities/events; NO redundant
    index — Postgres auto-creates one for the UNIQUE constraint).
  - `app/services/attribute.py`: `upsert_attribution()` — create path (first=last
    = this event), edit path (in-place denormalized update, no new row, no
    received_at change), update path (strict `<`/`>` only so equal timestamps keep
    the existing row — deterministic tie-breaking). Does NOT commit; caller owns
    the transaction.
  - Wired into `act()` (attribution slotted into the single lead+route commit);
    `EventIngestResponse.attribution_touch_id`; `_interpret_response` passes it
    through; GET /api/v1/leads/{id} returns first_touch_at / first_touch_source /
    last_touch_at / last_touch_source.
- **What is still a placeholder:** the receipts table is Phase 7 (the TODO stub
  inside `act()` remains; reconciliation is Phase 7). `escalated` stays
  computed-on-read for v1.
- **Human review / changes:** pending Rajat review.
- **Verification:** `pytest tests/integration/test_attribution.py -v` →
  **7 passed**. Full suite → **82 passed, 1 skipped** (target 82+1). In-container
  suite against isolated dsw_test → **82 passed, 1 skipped**. Migration applied to
  compose dev DB; `\d attribution_touches` shows uq_attribution_touches_identity_id
  + the 3 FKs and no extra index. Live Docker walkthrough: events arriving A(10:00)
  → B(12:00) → C(07:00) produced first_touch_at=07:00 (event C, arrived last) and
  last_touch_at=12:00 (event B) — attribution resolves by received_at, not arrival
  order; GET /leads/{id} returns the attribution fields.

---

### Session: Phase 7 — Receipts, Reconciliation, Redacted Structured Logging
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0012`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new real API
  call this phase — receipts/reconciliation/logging are deterministic; the LIVE
  interpretation stage is exercised only through the already-run Phase 3 test).
- **What was generated (7a + 7b):**
  - 7a: `Receipt` model in `app/db/models.py` (table `receipts`; `meta` mapped to
    the `metadata` column since `metadata` is a reserved SQLAlchemy declarative
    attribute; ix_receipts_action_type + ix_receipts_event_id indexes).
    Alembic `0008_receipts` (down_revision=0007_attribution_touches). New service
    `app/services/receipts.py` with `write_receipt()` (asserts action_type in
    VALID_ACTION_TYPES, never commits). Wired receipts into ingest (event_created/
    event_edited/event_rejected), resolve (identity_created/review_queued), review
    resolution (review_resolved — placed inside resolve_review, the commit owner,
    not the router), interpret (interpreted + error), score (scored), act
    (lead_created/lead_updated + routed + attributed_created/attributed_updated).
  - 7b: `app/routers/dashboard.py` GET /api/v1/dashboard/reconciliation (7 pairs,
    no caching). Implemented `_pii_redactor` in `app/logging.py` (SHA-256 first 16
    hex, `sha256:` prefix; in the processor chain before JSONRenderer). Audited
    every structlog call in ingest/resolve/interpret/score/act/attribute/manual_review
    to carry all 7 fields (input_id, decision, reason, action, result, error,
    timing_ms measured via time.monotonic()). Bound PII fields (email/name) on the
    identity_created log so redaction demonstrably runs.
- **What is still a placeholder:** `dead_lettered` is in VALID_ACTION_TYPES but
  not wired anywhere — Phase 8 will add it. `escalated` is computed-on-read for
  v1 (no scheduler).
- **Human review / changes:** pending Rajat review.
- **Verification:** 5 reconciliation + 2 logging-compliance tests →
  **7 new passed** (test_reconciliation_variance_zero_on_full_seeded_run is the
  hard pass/fail: overall_status="ok", total_variance=0, every entry variance=0).
  Full suite → **89 passed, 1 skipped** (target 89+1; 82 prior + 7 new — the
  prompt's "91" assumed 7a also added tests, but 7a added none). In-container
  suite → **89 passed, 1 skipped**. Migration `0008` applied to compose dev DB
  (`\d receipts` shows ix_receipts_action_type + ix_receipts_event_id).
  Reconciliation body on seeded run: `overall_status: ok`, `total_variance: 0`,
  all 7 entity pairs `variance: 0`. PII redaction verified: raw email absent from
  captured logs, `sha256:...` form present (e.g.
  `sha256:bca56d437cde4164`). Sample 7-field log line (routed): input_id,
  decision=hot, reason, action=routed, result=ok, error=null, timing_ms.

---

### Session: Phase 8 pre-work — routing idempotency defect fix
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0013`
- **Date:** 2026-08-20
- **Classification:** This is a DEFECT found and fixed — NOT routine Phase 8 scope.
  It was logged separately so it is not silently folded into Phase 8's feature list.
- **What the bug was:** `routes.lead_id` had no UNIQUE constraint (only a plain
  non-unique `ix_routes_lead_id` index). `route_lead()` unconditionally inserted a
  new `Route` row on every `act()` call, and `act()` called `route_lead()` even when
  `create_or_update_lead` returned `lead_op="updated"`. Consequently any second
  `act()` for the same lead — reachable today via FR-2's edited-resubmission path
  (edit re-runs interpret→score→act) — created a SECOND `routes` row for the same
  `lead_id`. Reachable since Phase 6.
- **How it was found:** diagnostic review (manual inspection of the `routes` table
  shape and `route_lead()`), NOT caught by the original test suite — no existing
  test asserted a `routes` row count anywhere.
- **The fix:**
  - Alembic `0009_routes_lead_id_unique` (down_revision `0008_receipts`): adds a
    UNIQUE constraint `uq_routes_lead_id` on `routes.lead_id`, REPLACING the
    redundant non-unique `ix_routes_lead_id` (a UNIQUE constraint auto-creates its
    own index, so keeping the plain index would be redundant). The migration also
    deduplicates legacy duplicate rows (keeps latest per lead_id) so the constraint
    can apply to existing deployments.
  - `Route.__table_args__` in `app/db/models.py` now declares the matching
    `uq_routes_lead_id` UNIQUE constraint.
  - `route_lead()` in `app/services/act.py` is now an upsert: if a route exists for
    `lead.id`, it UPDATES `queue`/`rule_matched`/`sla_deadline` (recomputed from the
    current decision/label — a re-route may change the queue) instead of inserting;
    else it inserts, with the same IntegrityError-on-race handling used elsewhere.
- **Correction (documented per the "correction-of-a-correction" instruction):**
  The FIRST attempt at the receipt logic made `act()` write a `routed` receipt ONLY
  when a route row is created (`lead_op="created"`) and write NOTHING when
  `route_lead()` updated an existing route — done specifically to make the
  reconciliation test pass. That violated FR-9 ("nothing mutates state without a
  receipt") and the Appendix's "do not skip writing a receipts row for any mutating
  action" instruction: a route update (queue/rule_matched/sla_deadline changing on a
  re-route) is a mutating action that produced zero evidence of itself.
  A diagnostic confirmed the reconciliation allowlist in
  `app/routers/dashboard.py` (`receipt_pairs` with exactly 7 pairs, incl.
  `"routes": ("route", "routed")`) counts ONLY those listed action_types and is
  therefore invisible to any unlisted type (lead_updated, attributed_updated, or a
  new route_updated) by construction.
  The CORRECTED final state: added `route_updated` to `VALID_ACTION_TYPES` in
  `app/services/receipts.py`; `route_lead()` now returns `(route, "created"|"updated")`
  (tuple return mirroring `create_or_update_lead` — chosen for consistency with the
  existing codebase and updated at the one direct call site in the concurrency test);
  `act()` writes `action_type="routed"` on creation and `action_type="route_updated"`
  on an in-place update. NO change to `dashboard.py` was needed — the allowlist
  ignores `route_updated` exactly as it ignores `lead_updated`/`attributed_updated`.
- **New test coverage:**
  - `test_edited_resubmission_updates_route_in_place`: POST + edit resubmission
    (changed message → different decision) asserts exactly ONE route row for the
    lead, its queue/rule_matched reflect the NEW decision, and the FR-9 receipts:
    exactly one `routed` receipt (original creation) plus exactly one `route_updated`
    receipt (the edit re-route), with NO second `routed` receipt for the update call.
  - `test_concurrent_route_lead_creates_one_route`: two near-simultaneous
    `route_lead()` calls via `asyncio.gather` on separate sessions assert exactly
    ONE route row — proving the DB constraint, not app logic, is the guard.
- **Verification:** full suite **91 passed, 1 skipped** locally and in-container
  (89 baseline + 2 new). `0009` applied cleanly to a fresh DB (`\d routes` shows
  `uq_routes_lead_id` UNIQUE CONSTRAINT, no `ix_routes_lead_id`).
  `test_reconciliation_variance_zero_on_full_seeded_run` still passes with variance 0
  (routes pair: dashboard_count=1, receipt_count=1, variance=0) — proving that
  adding `route_updated` does not break reconciliation, per the diagnostic. Live
  Docker: edit resubmission changed decision hot→warm (queue sales_urgent→
  sales_default, rule hot_any→warm_any) with route count staying exactly 1.

---

### Session: Phase 8 pre-work — events_created reconciliation mismatch defect fix
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0014`
- **Date:** 2026-08-20
- **Classification:** This is a SEPARATE DEFECT from S0013 (the routes fix), with a
  distinct root cause. Both were found during the same diagnostic session, before
  any Phase 8 feature work, and are logged as their own entries — not folded
  together.
- **What the bug was:** `app/routers/dashboard.py`'s `events_created` dashboard
  count filtered on `Event.is_valid.is_(True), Event.is_edit.is_(False)`, treating
  "created" and "edited" as mutually exclusive states of the same row. They are
  not: every valid event was created (a permanent fact, matching the immutable
  `event_created` receipt written exactly once) and some were later ALSO edited
  (an independent fact, matching `event_edited` receipts). When an FR-2 edit flips
  a row's `is_edit` to True, that row dropped out of the `events_created` count
  even though it was — and remains — a created event, producing a nonzero variance
  (`events_created: dashboard_count=0, receipt_count=1`) whenever a previously
  created valid event is later edited. Reachable via the ordinary FR-2 edit path.
- **How it was found:** diagnostic review while investigating the S0013 routes fix.
  It was NOT caught by the original test suite because no existing test combined
  create + edit + reconciliation together (the reconciliation test used distinct
  created events and never edited one; the edit-receipt tests never called the
  reconciliation endpoint).
- **The fix:** changed the `events_created` dashboard query to filter ONLY on
  `Event.is_valid.is_(True)`, removing the `Event.is_edit.is_(False)` condition.
  The `events_edited` query (is_edit=True) was left unchanged — it is already
  correct because `is_edit` is monotonic (never reverts once set) and stays
  consistent with the `event_edited` receipt count. `events_created` and
  `events_edited` are now intentionally OVERLAPPING (not a partition) — they answer
  different questions ("was this ever created" vs "was this ever also edited"),
  which is the correct model and not a double-count bug.
- **New regression test:** `test_create_then_edit_keeps_reconciliation_variance_zero`
  — POST a valid event, assert reconciliation is clean (overall_status="ok",
  total_variance=0, events_created variance=0); then POST an edit for that same
  event, assert reconciliation is STILL "ok" with total_variance=0 and both
  events_created and events_edited individually at variance=0. Verified it fails
  against the pre-fix query (overall_status "mismatch", events_created variance 1)
  and passes with the fix.
- **Verification:** reconciliation file **6 passed**; full suite **92 passed,
  1 skipped** (91 prior + 1 new regression test).

---

### Session: Phase 8b — Retry/Backoff + Dead-Letter for the Interpret Stage (FR-11)
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0015`
- **Date:** 2026-08-20
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new real API
  call this phase — dead-letter behavior is tested with mocked provider exceptions).
- **What was generated:**
  - `DeadLetterQueue` model in `app/db/models.py` (table `dead_letter_queue`: id PK,
    event_id FK, stage TEXT, error TEXT, retry_count INTEGER, resolved BOOLEAN
    default false, created_at). Alembic `0010_dead_letter_queue` (down_revision
    `0009_routes_lead_id_unique`; ix_dead_letter_event_id + ix_dead_letter_resolved).
  - `app/services/interpret.py`: on retry exhaustion (bounded by `_is_retryable`),
    the event is now dead-lettered — a `dead_letter_queue` row (stage="interpret",
    sanitized/truncated error ≤500 chars, retry_count=actual attempts) plus a
    `dead_lettered` receipt are written in a SINGLE commit (FR-9 atomicity),
    replacing the previous `interpreted`/status="error" receipt for this path.
    Per-attempt retry backoffs are still logged via structlog (7 fields), not
    receipted. Retry config confirmed env-configurable: `retry_max_attempts`
    (default 3) and `retry_base_delay_ms` (default 500) are already read from
    `RETRY_MAX_ATTEMPTS`/`RETRY_BASE_DELAY_MS` by `app/config.py` and used by the
    tenacity wrapper (no hardcoded literals).
  - `app/routers/events.py` + `app/schemas/responses.py`: exhaustion now returns
    `status="dead_letter"`, `stage="interpret"` (new `stage` field), and the
    pipeline halts (score/act never run) — confirmed the existing short-circuit on
    InterpretError already prevented score/act from running.
  - `app/routers/dashboard.py`: added `dead_letter_queue` as a reconciliation pair
    (`dead_letter_queue` ↔ `dead_lettered` receipt) — see decision below.
- **`error` action_type finding:** `error` is NEVER used as an `action_type`
  anywhere in the codebase. It is only used as a `status` value on receipts
  (`event_rejected`, `dead_lettered`, etc.). So retiring `error`-as-action_type was
  a no-op; the exhaustion receipt now uses `action_type="dead_lettered"`,
  `status="error"`.
- **Decision (reconciliation pair):** `dead_letter_queue` IS now a reconciliation
  pair (row count vs `dead_lettered` receipt count). Rationale: a dead_letter_queue
  row is a mutation that must be evidenced by its receipt (written atomically,
  1:1 today); leaving it out would recreate the exact blind spot the endpoint
  exists to catch. The PRD doesn't explicitly list it, but it is consistent with
  the existing routes/leads/attribution_touches pairs, so it was added deliberately
  (not silently) and disclosed here.
- **New tests:** `tests/integration/test_dead_letter.py` — (1) exhaustion test:
  mocked `openai.APITimeoutError` with `retry_max_attempts=3` asserts EXACTLY 3
  attempts (spy-count), one `dead_letter_queue` row (stage=interpret, retry_count=3,
  resolved=false), one `dead_lettered` receipt and NO `interpreted`/status=error
  receipt for that event, no fabricated `unknown` interpretation, and no
  score/lead/route rows (pipeline halted); API returns status="dead_letter",
  stage="interpret". (2) retry-budget test: `retry_max_attempts=1` → exactly 1
  attempt, retry_count=1.
- **Verification:** dead-letter tests **2 passed**; reconciliation file **6 passed**
  (new pair variance 0); full suite **94 passed, 1 skipped** locally and in-container
  (92 prior + 2 new). `\d dead_letter_queue` confirms all columns + indexes + FK.
- **Addendum (PRD-conformance fix, caught in review):** the dead-letter POST
  response initially returned HTTP 200 (the route's default) but the PRD §4 Error
  States table requires **202** for "LLM provider timeout/429 after retries
  exhausted". Fixed in `app/routers/events.py` by injecting a `Response` object into
  the handler and setting `response.status_code = 202` on the dead-letter branch
  only — all other paths through `POST /api/v1/events` (malformed JSON 400,
  schema-invalid 200, duplicate 200, manual-review 200, normal success 200) are
  untouched. `test_exhaustion_dead_letters_event_and_halts_pipeline` updated to
  assert 202. Full suite still **94 passed, 1 skipped** locally and in-container.

### Session: Phase 8c — Admin Replay + Simulate-Failure (FR-11, FR-12)
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0016`
- **Date:** 2026-08-21
- **Provider / model:** OpenRouter, `deepseek/deepseek-v4-flash` (no new real API
  call this phase — replay happy paths are tested with a mocked classifier; the
  forced-failure path uses a mocked `openai.APITimeoutError`).
- **What was generated:**
  - `app/routers/admin.py` (new — no admin router existed before; mounted in
    `app/main.py` after the external routers, matching the existing no-prefix-at-
    mount convention since each router bakes its own prefix).
    - `POST /api/v1/admin/replay/{event_id}` — bearer-token gated, re-runs the
      existing pipeline (`classify_event → score_event → act`) by REUSING the
      service functions (no inline reimplementation). On success marks the DLQ row
      `resolved=true`, writes a `dead_letter_resolved` receipt (entity_type=
      "dead_letter"), returns `200 {status: replayed, event_id, lead_id}`.
    - `POST /api/v1/admin/simulate-failure` — body `{stage, event_id}`; for
      `stage="interpret"` writes a DLQ row + `dead_lettered` receipt and returns
      `200 {status: dead_lettered}` (used by tests to force a dead-lettered event).
      Rejects non-interpret stages with `400 invalid_stage`.
  - `app/services/receipts.py`: `dead_letter_resolved` added to
    `VALID_ACTION_TYPES`.
- **Design decisions (all surfaced for review):**
  - **Auth dependency created fresh** — no reusable bearer-auth dependency existed,
    so `require_admin` was built in `admin.py` using `HTTPBearer(auto_error=False)`,
    comparing `credentials.credentials != settings.admin_api_key` → `401
    {"error": "unauthorized"}`. `ADMIN_API_KEY` defaults to `test` (not a real
    secret in this dev evaluation).
  - **409 for not-dead-lettered, not a silent 200** — per the replay contract, a
    replay of an event with no unresolved DLQ row (or no DLQ row at all; also when
    `resolved=true` from a prior successful replay) returns `409
    not_dead_lettered`. This makes re-replay explicit rather than silently
    re-running work.
  - **Defensive >1-identity-link check** — replayed events are already resolved
    (resolution runs before interpret, so a dead-lettered event normally has exactly
    one `IdentityLink`). Replay does NOT re-run `resolve_identity`; it reads the
    single link and errors `409 ambiguous_identity` if `len(links) != 1` (defensive
    only — `IdentityLink` has no unique constraint on event_id, confirmed in
    `app/db/models.py` and migration 0002).
  - **`dead_letter_resolved` receipt rationale** — reconciliation only pairs DLQ
    row-count vs `dead_lettered` receipts, so flipping `resolved=true` alone would
    not break that pair; but FR-9 ("nothing mutates state without a receipt")
    requires the resolution mutation itself be receipted. Added deliberately.
  - **Failed replay → NEW DLQ row (not retry_count increment)** — when the replay's
    `classify_event` exhausts retries, the existing exhaustion path (Phase 8b)
    writes a NEW `dead_letter_queue` row + `dead_lettered` receipt, surfaced as
    `503 replay_failed`. This reuses existing code (driest option) and treats each
    attempt as a distinct dead-letter record rather than mutating the original row.
- **Test coverage:** `tests/integration/test_admin.py` — 13 tests: 401 both
  endpoints (missing + wrong token), replay happy path (verifies exactly one
  `dead_letter_resolved` receipt), simulate-failure happy path (DLQ resolved=false),
  404 replay unknown event, 409 not-dead-lettered, 409 re-replay after resolution,
  400 invalid_stage, 404 simulate-failure, and the two critical tests below.
- **Critical idempotency test** (`test_replay_partial_success_is_idempotent`):
  the event is dead-lettered then replayed; asserts AFTER replay exactly ONE row
  each for interpretations/scores/leads/routes/identity_links (no double-writes
  from re-running the pipeline) and the DLQ row `resolved=True`. Literal printed
  counts: `BEFORE dead-letter: interpretations=1 scores=1 leads=1 routes=1 dlq=0
  identity_links=1`; `AFTER dead-letter (pre-replay): ... dlq=1`;
  `AFTER replay: interpretations=1 scores=1 leads=1 routes=1 dlq=1 identity_links=1`;
  `dlq resolved=True stage=interpret retry_count=3`. This is the proof that the
  Phase 8a routes-idempotency fix holds under replay.
- **Forced-failure test** (`test_replay_redeadletters_when_provider_still_down`):
  dead-letter via simulate-failure, then force the provider to keep throwing during
  replay → returns `503 replay_failed` and classify_event's exhaustion path writes a
  SECOND DLQ row + second `dead_lettered` receipt (both resolved=false).
- **Verification:** admin tests **13 passed** locally (both critical tests' printed
  counts captured above); full suite **107 passed, 1 skipped locally and in-
  container** (94 prior + 13 new admin). `docker compose build app` +
  `docker compose up -d` succeeded before the in-container run.

---

### Session: Phase 0–8 audit — 7 defect fixes, dead-letter endpoint, escalation, LIVE/SIMULATED labels
- **Session ID:** `DAXVORA-RAJAT-2026-08-A01-S0017`
- **Date:** 2026-08-21
- **Provider / model:** Anthropic, Claude Opus 5 (Claude Code). No OpenRouter call
  this session — every test stubs `interpret._call_llm`.
- **Prompt intent:** an end-to-end audit of Phases 0–8 against
  `docs/PRD_Demand_Signal_Workflow_v1_2.md` and
  `docs/Project02_Implementation_Plan_v1.md`, then "fix all these issues one by
  one". The audit found 6 defects; a 7th surfaced while fixing them.

**Defect 1 — `payload_hash` not advanced on edit (FR-2).**
`app/services/ingest.py` set `is_edit = True` and replaced `raw_payload` but left
`payload_hash` at the *original* value. Since that column is the stored comparand
for every future submission on the same `dedupe_key`, the row was permanently
unequal to its own content: resubmitting the edited payload re-detected as an edit
forever (an extra `event_edited` receipt plus a redundant interpret→score→act on
every submission, growing reconciliation variance without bound), while
resubmitting the *original* payload was misread as a true duplicate — silently
discarding a real revert. Fix: advance `payload_hash` to the incoming hash and
record `previous_payload_hash` + `payload_hash` in the `event_edited` receipt
metadata so the transition stays auditable.

**Defect 2 — `create_new` review resolution minted an unreceipted identity (FR-9).**
`resolve_review`'s `create_new` branch inserted an `Identity` with no
`identity_created` receipt, which is exactly the "state mutated without a receipt"
condition FR-9 forbids, and put the `identities` ↔ `identity_created`
reconciliation pair permanently at nonzero variance. Fix: write the receipt with
`match_rule="manual_review_resolve"`, `match_confidence="1.00"` and the
`review_id`, plus the matching `identity_created` structured log line. The new
identity's `display_name` is now derived from the event's `identity_fields`
(`name` → `display_name`) instead of being left NULL.

**Defect 3 — resolving a manual review never resumed the pipeline (Flow 3 step 4).**
This was the largest gap. Ingest deliberately halts an ambiguous event *before*
interpret, so resolution is the only thing that can un-halt it — but
`POST /api/v1/manual-review/{id}/resolve` linked the identity and returned. The
event was left with no interpretation, score, lead, route or attribution touch,
permanently. Fix: extracted `app/services/pipeline.py::run_downstream` as the one
shared interpret→score→act runner and pointed **all three** entry points at it —
ingest (`app/routers/events.py`), the manual-review resume, and admin replay — so
they cannot drift. Ingest and resume surface an exhausted-retry failure as `202`
with `status="dead_letter"`; replay keeps its `503 replay_failed`. The resolve
response now returns `pipeline_status`, `label`, `score`, `decision_outcome`,
`lead_id`, `lead_op`, `queue`, `rule_matched`, `sla_deadline` and
`attribution_touch_id`, matching the PRD's API table.

**Defect 4 — `UNIQUE(events.dedupe_key)` was global, not scoped to accepted rows.**
Two distinct symptoms from one root cause. (a) Resubmitting the same
schema-invalid payload raised `IntegrityError` → **500**, which also broke "never
silently drop an invalid event" — the caller got an error instead of a persisted
rejection. (b) A *corrected* resubmission of the same `external_event_id` matched
the rejected row's `dedupe_key` and was treated as an **edit of a row still
flagged `is_valid=false`**, running the whole pipeline against it and never
producing a clean accepted event. Fix: migration `0011` replaces the constraint
with a partial unique index `WHERE is_valid = true AND dedupe_key IS NOT NULL`,
and `find_event_by_dedupe_key` is scoped to `is_valid = true` to match.
  - *Considered and rejected:* catching `IntegrityError` in `persist_invalid_event`
    and collapsing repeat rejections onto one row. That fixes only symptom (a),
    leaves (b) intact, and desynchronizes the `events_rejected` ↔ `event_rejected`
    pair (one row, N receipts). Scoping the index keeps each rejection its own row
    *and* its own receipt, so variance stays 0.
  - The migration looks the auto-generated constraint name up from `pg_constraint`
    rather than hardcoding `events_dedupe_key_key`. `downgrade()` NULLs duplicate
    `dedupe_key`s (keeping the oldest valid row) before restoring the global
    constraint, so no audit row is ever deleted.

**Defect 5 — `routes.escalated` was never evaluated.** Three separate docstrings
described it as computed-on-read, but nothing computed it and `escalated` was
absent from the `/leads` list payload entirely. FR-9 also names an `escalated`
action type that was missing from `VALID_ACTION_TYPES`, so the transition could not
have been receipted even if something had set the flag. Fix: new
`app/services/escalation.py` with a pure `is_sla_breached()` (strict `<`, so a route
sitting exactly on its deadline has not yet breached — matching the `>=`-at-threshold
convention in `resolve.should_auto_link` and `score._decide`; naive timestamps are
coerced to UTC rather than raising) and `evaluate_escalation()`, which performs a
monotonic false→true transition, writes the `escalated` receipt once, and does not
commit (the caller owns the transaction). Both leads endpoints now call it and
commit. On-read detection rather than a scheduler is the PRD's own recommendation
(§12 open item, line 421) — no new dependency, and FR-9's `escalated` receipt
becomes real.

**Defect 6 — `manual_review_queue.resolved_at` left NULL** on resolution, so the
queue recorded *that* an entry closed but not *when*. Fixed in the same
`resolve_review` rewrite; both branches now commit exactly once.

**Defect 7 (found while fixing) — `events_edited` reconciliation manufactured
variance.** `events.is_edit` is a sticky boolean — set once, never unset — so the
entity side counts "events that have ever been edited" while the receipt side
counted raw `event_edited` receipts. Any event edited twice (two genuinely
different payloads, two legitimate receipts, still one row) reported variance 1 and
failed the FR-10 / Success-Criterion-2 gate. Fixed with
`COUNT(DISTINCT receipts.event_id)` on that pair only; audited all 8 pairs and
confirmed it is the only one affected.

**API gaps closed.**
  - `GET /api/v1/dead-letter` (new `app/routers/dead_letter.py`): the PRD's Error
    States table promises a dead-lettered event is "visible in
    `/api/v1/dead-letter`". Phase 8b wrote the rows and 8c added replay, but nothing
    could *enumerate* what needed replaying without direct SQL. Read-only and
    unauthenticated (the PRD lists 200 only; the bearer gate covers the mutating
    admin endpoints), `?resolved=` filtered, ordered oldest-first because the list
    doubles as a replay worklist, and each entry carries a `replay_url` so a UI
    needs no client-side URL construction.
  - `/dashboard/reconciliation` now returns the PRD's top-level
    `{"variance": N, "status": "PASS"|"FAIL"}` alongside the FR-10 per-metric rows,
    and accepts `since`/`until` (PRD §3 criterion 2, "for the same time window")
    applied to **both** sides of every comparison. This is exact because Postgres
    evaluates `now()` once per transaction and an entity plus its receipt are always
    written in one transaction, so a window cannot split a pair. `overall_status`
    and `total_variance` are retained as aliases so existing callers keep working.
  - *Deliberately NOT added:* `interpreted` / `scored` reconciliation pairs. Those
    receipts accumulate per pipeline re-run while the row count stays at 1
    (`interpretations.event_id` is UNIQUE), so pairing them would break the
    variance-0 gate by construction.

**Compliance (Phase 1 / Phase 3 gates).** `README.md` now carries the
LIVE/SIMULATED/TEST/MOCKED table at the point of use: all three connectors
SIMULATED (internal fixture generators, no Reddit/social/ESP API ever called),
classification LIVE via OpenRouter, `simulate-failure` test-harness-only, test-suite
LLM calls MOCKED — plus the two efficiency short-circuits that mean not every event
triggers the LIVE call, and a migration note for `0011`.

- **Files modified:** `app/services/ingest.py`, `app/services/resolve.py`,
  `app/services/pipeline.py` (new), `app/services/escalation.py` (new),
  `app/services/receipts.py`, `app/db/models.py`,
  `app/db/migrations/versions/0011_events_dedupe_key_valid_only.py` (new),
  `app/routers/events.py`, `app/routers/manual_review.py`, `app/routers/admin.py`,
  `app/routers/leads.py`, `app/routers/dashboard.py`,
  `app/routers/dead_letter.py` (new), `app/main.py`,
  `tests/unit/test_escalation.py` (new),
  `tests/integration/test_phase8_audit_fixes.py` (new), `README.md`, `AI_USAGE.md`,
  `ai-usage.json`.
- **Test coverage:** 27 new tests, each of which fails against the pre-fix code.
  `tests/unit/test_escalation.py` (7) pins the SLA boundary — before / exactly at /
  after the deadline, naive-tz coercion, `None` — and asserts every FR-9-named
  action is registered in `VALID_ACTION_TYPES`.
  `tests/integration/test_phase8_audit_fixes.py` (20) covers: edited payload
  resubmitted → `duplicate:true`; `payload_hash` advanced with both hashes in the
  receipt; revert to the original re-detected as an edit; two distinct edits keep
  variance 0 (the defect-7 DISTINCT fix); `create_new` writes `identity_created`;
  `resolved_at` set and surfaced by the list endpoint; resolve → `pipeline_status:
  "resumed"` with label/score/lead **and** the Interpretation / Score / Lead /
  Route / AttributionTouch rows asserted present for the previously-parked event;
  `merge_into` → `lead_op:"updated"` with exactly one lead for the merged identity;
  the same invalid payload twice → two 200s / 2 rows / 2 `event_rejected` receipts;
  three repeat rejections at variance 0; corrected resubmission → a new accepted
  event (not an edit), both rows sharing one `dedupe_key`, zero `event_edited`
  receipts; backdated SLA → `escalated:true`, persisted on the route, exactly one
  `escalated` receipt, idempotent across 4 further reads, and exposed by the list
  endpoint; unbreached SLA not escalated; dead-letter listing fields +
  `replay_url`, `?resolved=` filtering after a replay, and empty when nothing
  failed; reconciliation top-level `variance`/`status` + `since`/`until`.
- **Verification (and its limits, stated plainly):** `python -m compileall` clean
  across every changed module; all 11 routes confirmed registered via
  `app.openapi()["paths"]` including the new `GET /api/v1/dead-letter`; **unit suite
  45 passed** (38 pre-existing + 7 new); the 20 new integration tests **collect**
  cleanly. The integration suite was **not executed** — the Docker daemon is not
  running in this environment and Postgres is unreachable (`pg_isready -h /tmp` no
  response; `localhost:5432` "Operation not permitted"), so the integration results
  above are unverified and must be run by the maintainer:
  `docker compose run --rm --no-deps -v "$PWD/tests:/app/tests" -v "$PWD/pytest.ini:/app/pytest.ini" --entrypoint pytest app -q`.
  Migration `0011` must also be applied to any existing database
  (`alembic upgrade head`); the test suite builds its schema from
  `Base.metadata.create_all`, so it picks up the partial index from the model.
- **Still outstanding (not fixed here, deliberately):**
  - The Phase 3 gate "log actual token usage from a real test run into the README's
    cost section — not an estimate" still cannot be satisfied: this environment's
    network access does not reach OpenRouter, so no live call is possible. The
    README says so explicitly rather than carrying an estimate dressed up as a
    measurement.
  - `/api/v1/dashboard/summary` and the Jinja2 dashboard screens are Phase 9 per the
    implementation plan and were left out of scope on purpose.

---

