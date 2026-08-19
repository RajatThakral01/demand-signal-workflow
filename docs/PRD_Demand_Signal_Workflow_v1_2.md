# Product Requirements Document (PRD)

---

## 0. Document Info
- **Project Name:** Demand-Signal Scoring, Routing & Attribution Workflow (DAXVORA Capability Assessment — Project 02)
- **Version:** v1.2
- **Date:** 2026-08-20
- **Owner:** Rajat (Candidate) — Evaluation ID `DAXVORA-RAJAT-2026-08-A01`

**Changelog (v1.0 → v1.1)**
- Defined explicit behavior for an edited event resubmitted under the same `external_event_id` (previously silently treated as an exact duplicate no-op). See FR-2, Flow 2, Section 6, Section 10, Section 11.
- Denormalized `source` and `campaign_id` onto `attribution_touches` so attribution is directly inspectable without a join. See FR-8, Section 6.
- Added a `review_resolved` receipt so manual-review outcomes are reconciliation-complete, not just the queuing step. See FR-9, Section 6.
- Added explicit LIVE labeling requirement for the real Claude API classification call, distinct from the SIMULATED connectors. See Section 6 (Third-Party Integrations) and Section 9.

**Changelog (v1.1 → v1.2)**
- Written approval from Krishnam obtained for real (LIVE) API usage in the interpretation stage — no longer an open spend-approval risk. See Section 9.
- Switched the interpretation stage's LLM access from a direct Anthropic API key to an OpenRouter API key (OpenAI-compatible endpoint, model routed through OpenRouter). See Section 6 (Tech Stack, Third-Party Integrations, Environment Variables) and Appendix.

---

## 1. Project Overview

**Problem Statement**
Founder-led businesses receive demand signals (form fills, social/community mentions, email engagement) from multiple disconnected sources. Without a unifying system, the same person is treated as multiple contacts, leads get created twice on retries, scoring is inconsistent, and nobody can prove where a lead actually came from. This project builds a source-agnostic backend workflow that turns raw, possibly-duplicated, possibly-incomplete demand signals into deterministic, auditable, exactly-once lead records with traceable attribution — and proves, via reconciliation, that nothing was lost or double-counted.

**Goal / Vision**
"Done" means: three synthetic signal sources can each independently fire the same underlying contact through the pipeline (including duplicates, retries, and mid-pipeline failures), and the system produces exactly one lead per real contact, with a score/decision that is reproducible, a route that is traceable to an explicit rule, an attribution record that survives replay, and a dashboard whose totals reconcile exactly against the underlying receipt log.

**Target Users**
- **Primary (real):** The DAXVORA evaluator, reviewing this as a technical assessment — they will run the seeded test pack, inspect logs/receipts, and re-run duplicate/failure scenarios.
- **Secondary (simulated persona this system is designed for):** A non-technical founder at a 2–20 person business who wants to trust that "one dashboard number" without manually reconciling spreadsheets. No actual founder-facing UI polish is required — the dashboard exists to prove correctness, not to sell the product (that's Project 03's job).

**Success Criteria**
1. Replaying any of the 50+ seeded fixture events (including exact duplicates, retried-after-timeout duplicates, and edited variants) never changes the total lead count — verified by an automated test, not manual inspection.
2. `GET /api/v1/dashboard/reconciliation` reports `variance: 0` between displayed dashboard counts and the underlying `receipts` table for the same time window, on every test run.
3. Every event that reaches a terminal state (`valid`/`invalid`, `matched`/`manual_review`, `label`/`unknown`, `scored`/`insufficient_data`) has a human-readable reason string attached — zero silent drops, verified by asserting `reason IS NOT NULL` across the full seeded run.
4. Full local setup (`docker compose up` + one seed command) completes in under 5 minutes on a clean machine with no manual steps beyond copying `.env.example` to `.env`.

---

## 2. Scope

**In Scope (v1 features)**
- [ ] Three seeded, synthetic signal connectors: web form submission, community/social mention, email/campaign engagement event — each a local fixture generator hitting the real ingestion API, not a live third-party integration.
- [ ] Versioned event schema (Pydantic, discriminated by `source`) with validation, campaign metadata, stable `event_id`, identity fields, timestamp, `consent` flag.
- [ ] Deterministic identity resolution with a documented, versioned rule set; ambiguous matches routed to a manual-review queue, never force-merged.
- [ ] Intent/pain/topic classification via LLM call (Claude API) with mandatory `label`, `confidence`, `reason`, and an explicit `unknown` outcome for insufficient signal text.
- [ ] Versioned, file-based scoring policy producing `score`, contributing `features`, `policy_version`, and `decision`, with documented tie-breaking and insufficient-data handling.
- [ ] Exactly-once lead creation/update per canonical identity, explicit rule-based routing with a fallback queue, and SLA/escalation timestamps.
- [ ] First-touch/last-touch attribution with a documented conflict-resolution policy, surviving retries and out-of-order delivery.
- [ ] A minimal read-only dashboard (server-rendered, no separate frontend build) showing counts by source/status/decision, plus a reconciliation endpoint/view.
- [ ] Structured JSON-line logs and a `receipts` table covering every mutating action.
- [ ] Dead-letter handling with bounded retry/backoff for simulated connector/provider failures, and a safe replay path.
- [ ] Full automated test suite covering the acceptance checks in Section 11.
- [ ] `AI_USAGE.md`, `ai-usage.json`, `.env.example`, README, architecture diagram, and a plan-vs-built writeup (required by the assessment brief, not just this PRD).

**Out of Scope (explicitly NOT building now)**
- [ ] Real integrations with a live CRM, live social platform API, or live email/marketing platform — synthetic fixture adapters only.
- [ ] Authentication/authorization UI, multi-user roles, or multi-tenant support (single-evaluator local use; admin endpoints may sit behind a single static API key from `.env`, not a login system).
- [ ] A message broker / distributed queue (Kafka, RabbitMQ, SQS) — an in-database outbox/dead-letter table stands in for this at assessment scale.
- [ ] Horizontal scaling, load balancing, or production cloud deployment — this project runs locally via Docker Compose. (Project 03, the website, is the one with a public HTTPS requirement — this backend does not need one.)
- [ ] Sending real emails, posting to real social platforms, or writing to a real CRM.
- [ ] A polished/branded frontend — the dashboard is functional evidence, not a marketing surface.

**Future Considerations (not built now, but design should allow for it)**
- [ ] Swapping fixture adapters for real connectors (Typeform/webhook, real subreddit stream via Project 01's Reddit agent, SendGrid/Mailchimp webhooks) behind the same ingestion schema.
- [ ] Multi-tenant support (a `tenant_id` column exists nowhere yet, but table PKs should not assume single-tenant forever — avoid hardcoding assumptions that would require a schema rewrite).
- [ ] Replacing the in-DB outbox with a real queue if throughput ever requires it.
- [ ] A learned/ML scoring model replacing or augmenting the versioned rules-based policy, still required to remain explainable.

---

## 3. User Flows / Use Cases

### Flow 1: Clean new signal → lead created
1. A fixture adapter POSTs a valid `web_form` event to `/api/v1/events`.
2. System validates schema, computes `dedupe_key`, persists to `events` as `is_valid=true`.
3. Identity resolution finds no existing match on email/phone → creates a new `identities` row.
4. Interpretation stage calls the LLM, returns `label="pricing_inquiry"`, `confidence=0.86`, `reason="..."`.
5. Scoring stage applies policy v1, returns `score=78`, `decision="warm"`.
6. Act stage creates a new `leads` row (status `new`), routes it to the `sales_default` queue per an explicit rule, sets an SLA deadline.
7. A `receipts` row is written for the lead-creation action.
8. Edge case: if the LLM call fails, the event is written to `dead_letter_queue` at stage `interpret` with bounded retry, not silently skipped.

### Flow 2: Duplicate / retry never creates a second lead
1. The same `web_form` event (same `external_event_id` + `source`) is POSTed again (simulating a client retry after a timeout).
2. System computes the same `dedupe_key`, finds an existing `events` row → does **not** re-run the pipeline; returns the original result with `duplicate: true` in the response.
3. No new `leads`, `routes`, or `receipts` rows are created.
4. Edge case: if the *contact* re-submits a **new** event (different `external_event_id`, same email) after a lead already exists for that identity, the system updates the existing lead (adds an attribution touch) rather than creating a second lead — this is an *update*, not a duplicate, and must be distinguished from Flow 2's exact-replay case.
5. Edge case — **edited resubmission** (same `external_event_id`, different payload): the system computes `payload_hash` on every submission. If `dedupe_key` matches an existing event but `payload_hash` differs, this is **not** a true duplicate — it is an edit. The system updates the existing `events` row's `raw_payload`/`identity_fields`, sets `is_edit=true`, writes a `receipts` row with `action_type="event_edited"`, and re-runs interpret → score → act for that event so downstream state reflects the corrected content (e.g., a corrected email may change identity resolution; corrected text may change classification/score). This must remain distinguishable from Flow 2's exact-replay no-op in both behavior and receipts.

### Flow 3: Ambiguous identity → manual review, never auto-merged
1. A `social_mention` event arrives with only a display name (no email/phone) that fuzzy-matches an existing identity's name at low confidence.
2. Identity resolution stage does **not** auto-merge — it writes a `manual_review_queue` row with `reason="fuzzy_name_match_below_threshold"` and the event proceeds no further in the pipeline (interpretation/scoring/act are all skipped until resolved).
3. A reviewer calls `POST /api/v1/manual-review/{id}/resolve` with a decision (`merge_into: <identity_id>` or `create_new`).
4. Only after resolution does the event continue through interpret → score → act.

### Flow 4: Provider/connector failure recovers safely
1. The interpretation stage's LLM call times out or returns a 5xx/429.
2. The event is retried with bounded exponential backoff (max 3 attempts, per Section 4 config).
3. If all retries fail, the event moves to `dead_letter_queue` with `stage="interpret"` and a visible error — it is not lost, and it is not silently scored as `unknown` without a retry having been attempted.
4. An admin can call `POST /api/v1/admin/replay/{event_id}` once the provider recovers; replay must be safe (idempotent) even if some earlier stage had partially succeeded.

### Flow 5: Dashboard reconciles to receipts
1. Evaluator runs the full seeded fixture pack (including duplicates and forced failures).
2. Evaluator calls `GET /api/v1/dashboard/summary` for counts by source/status/decision.
3. Evaluator calls `GET /api/v1/dashboard/reconciliation`, which independently sums `receipts` for the same window and diffs it against the dashboard's own counts.
4. Pass condition: `variance == 0`. Any nonzero variance is a defect, not a rounding note.

---

## 4. Functional Requirements

| ID | Requirement | Input | Output | Validation Rules |
|----|-------------|-------|--------|-------------------|
| FR-1 | The system shall validate every incoming event against a versioned, source-specific Pydantic schema before persisting it. | Raw JSON payload + `source` | `event_id`, `is_valid`, `invalid_reason` (if any) | Unknown/missing required fields → `is_valid=false`, event still persisted (isolated, not dropped), with `invalid_reason`. |
| FR-2 | The system shall compute a deterministic `dedupe_key` = hash(`source` + `external_event_id`) and a `payload_hash` = hash(canonicalized body). A matching `dedupe_key` with a matching `payload_hash` is a true duplicate (no-op). A matching `dedupe_key` with a **different** `payload_hash` is an edit: update the existing event and re-run interpret → score → act. | Event payload | Duplicate: existing event record, `duplicate: true`. Edit: updated event record, `is_edit: true`, re-run pipeline result | Unique DB constraint on `dedupe_key`; must be enforced at the DB level, not just application logic (protects against race conditions). Edits are never silently dropped — every edit produces an `event_edited` receipt (see FR-9). |
| FR-3 | The system shall resolve identity using a documented, versioned rule set: exact email match (high confidence, auto-link) → exact normalized phone match (high confidence, auto-link) → fuzzy name+company match (low confidence, manual review only). | Event identity fields | `identity_id` (linked or newly created) OR a `manual_review_queue` entry | No rule may auto-merge below a configured confidence threshold (default 0.85). Threshold must be in the policy config, not hardcoded inline. |
| FR-4 | The system shall classify each valid, resolved event's free text for pain/topic/intent using an LLM call at `temperature=0`, with a pinned model version and prompt version recorded per result. | Event free-text field(s) | `label`, `confidence`, `reason`, `model_version`, `prompt_version` OR `label="unknown"` | Text under a configured minimum length (default 8 tokens) is classified `unknown` without calling the LLM (saves cost — see Section 5 Performance and the Efficiency scoring rubric in the assessment brief). |
| FR-5 | The system shall score every classified event using a versioned policy file, producing `score`, `features` (the inputs that produced it), `policy_version`, and `decision`. | Interpretation output + event metadata | `score` (0–100), `decision` (`hot`/`warm`/`cold`/`needs_review`) | Ties resolved by the rule documented in the policy file (see Section 6 example). Insufficient data (e.g., `label="unknown"`) must resolve to `needs_review`, never a fabricated numeric score. |
| FR-6 | The system shall create-or-update exactly one lead per canonical identity, using a DB-level unique constraint on `identity_id` as the idempotency anchor. | `identity_id`, score/decision | `lead_id`, `status` | Lead creation/update must happen inside a single DB transaction with the receipt write (see FR-9) — both succeed or both roll back. |
| FR-7 | The system shall route every created/updated lead to a queue using an explicit, ordered rule table, falling back to a default queue if no rule matches. | `decision`, `label`, lead metadata | `queue`, `rule_matched`, `sla_deadline` | Every route must record which rule fired (`rule_matched`) — "it just went to sales" is not traceable enough. |
| FR-8 | The system shall record first-touch and last-touch attribution per identity, updating last-touch on every new (non-duplicate) event and never overwriting first-touch. | Event + identity + timestamp | Updated `attribution_touches` record, including `source` and `campaign_id` denormalized from the originating event so attribution is directly inspectable without a join | Conflict policy (documented in Section 6): first-touch is immutable once set; last-touch is always the most recent *valid, non-duplicate* event by `received_at`, ties broken by `event_id` insertion order. An edit (FR-2) updates the existing touch's denormalized fields in place rather than creating a new touch. |
| FR-9 | The system shall write a `receipts` row for every mutating action (event rejected, event edited, lead created, lead updated, routed, escalated, sent to manual review, manual review resolved, sent to dead-letter). | Action context | `receipts` row | Receipts are the reconciliation source of truth — nothing mutates state without one, including a manual-review resolution outcome, which was previously only receipted at the queuing step. |
| FR-10 | The system shall expose a reconciliation endpoint that independently recomputes dashboard totals from `receipts` and reports variance. | Time window | `{dashboard_count, receipt_count, variance}` per metric | `variance` must be `0` under all seeded test scenarios, including duplicates and forced failures. |
| FR-11 | The system shall retry transient provider failures (LLM timeout/429/5xx) with bounded exponential backoff, then move to dead-letter on exhaustion. | Failed stage call | Retry attempts logged; `dead_letter_queue` row on exhaustion | Max 3 attempts, base delay 500ms, jitter enabled (configurable via env). No unbounded retry loops. |

**Error States**

| Scenario | Expected Behavior | Error Message |
|----------|--------------------|----------------|
| Malformed JSON body | Reject at HTTP layer, event not persisted (no valid schema to isolate) | `400 {"error": "malformed_json"}` |
| Valid JSON, fails schema | Persist as `is_valid=false` with reason, return 200 (accepted-but-invalid, not a client error — the connector isn't at fault for a genuinely malformed real-world event) | `200 {"event_id": "...", "is_valid": false, "invalid_reason": "missing_identity_fields"}` |
| Duplicate `dedupe_key` | Return original result, no new processing | `200 {"event_id": "...", "duplicate": true}` |
| LLM provider timeout/429 after retries exhausted | Event → dead-letter, visible in `/api/v1/dead-letter` | `202 {"event_id": "...", "status": "dead_letter", "stage": "interpret"}` |
| Ambiguous identity match | Event parked at manual review, pipeline halts for that event only | `200 {"event_id": "...", "status": "manual_review", "review_id": "..."}` |
| Reconciliation variance nonzero | Surfaced clearly, not hidden — this is a test failure, not a warning to log and ignore | `200 {"variance": 3, "status": "FAIL"}` (the field itself signals failure; no exception needed) |

---

## 5. Non-Functional Requirements

- **Performance:** Single event should complete ingest→act (excluding manual-review pauses) in under 3 seconds under seeded test load, including the LLM call. Dashboard/reconciliation endpoints must respond in under 1 second for the seeded dataset size (assume ≤500 events for assessment purposes).
- **Security:** All secrets (DB URL, `OPENROUTER_API_KEY`, admin API key) via environment variables only — never committed, never logged. Admin/replay/simulate-failure endpoints require a static bearer token from env (`ADMIN_API_KEY`) since there's no full auth system in scope. PII (email, phone, name) must be redacted in structured logs (log the hash or a masked form, not raw values) — raw PII may live in the DB (it's needed for identity resolution) but never in logs or `AI_USAGE.md`/evidence exports.
- **Accessibility:** N/A for the dashboard's WCAG conformance specifically (this is an internal evaluator tool, not the public-facing Project 03 site) — but it should still be plain semantic HTML (real `<table>`, real headings) so it's readable without JS and inspectable by the evaluator without friction.
- **Browser/Device Support:** Any modern browser for the read-only dashboard; no mobile-specific work required. This is not the responsive/Lighthouse-scored deliverable (that's Project 03).
- **Scalability:** Design should not hardcode single-tenant or single-instance assumptions that would be expensive to unwind later (see Section 2 Future Considerations), but no actual scaling work is required for v1 — a single Docker container plus a single Postgres instance is the target deployment shape.

---

## 6. Technical Specifications

**Tech Stack**
- Frontend: None separate — a minimal server-rendered dashboard using FastAPI + Jinja2 templates, plain HTML/CSS, no JS framework, no build step.
- Backend: Python 3.11+, FastAPI, Pydantic v2 (schema validation + versioning via discriminated unions), SQLAlchemy 2.0 (async) + Alembic for migrations, asyncpg driver, structlog for structured JSON logging, tenacity for retry/backoff, httpx for the fixture adapters' outbound calls to the API itself.
- Database: PostgreSQL 15+, run via Docker Compose for reproducible local setup.
- Hosting/Deployment: Local only, via `docker compose up`. No cloud hosting required or expected for this project (unlike Project 03).
- Key libraries/frameworks: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`, `pydantic>=2`, `pydantic-settings`, `structlog`, `tenacity`, `openai` (official SDK, pointed at the OpenRouter base URL, for the interpretation stage — OpenRouter exposes an OpenAI-compatible API), `pytest`, `pytest-asyncio`, `httpx` (also used in tests).

**Architecture**
Layered monolith, single FastAPI process, fully async:
`API routers → service layer (ingest / resolve / interpret / score / act / prove, one module each) → repository layer (SQLAlchemy) → PostgreSQL`.
Durability/idempotency is achieved via DB-level unique constraints and single-transaction writes, not a message broker — an in-database `dead_letter_queue` table plays the role of a durable retry queue at this scale (documented explicitly as a v1 simplification in Section 2 and the assessment's required plan-vs-built writeup).

**Folder/File Structure**
```
project-root/
  ├── app/
  │   ├── main.py                 # FastAPI app instantiation, router mounting
  │   ├── config.py               # pydantic-settings, reads .env
  │   ├── db/
  │   │   ├── models.py           # SQLAlchemy ORM models
  │   │   ├── session.py          # async engine/session factory
  │   │   └── migrations/         # Alembic
  │   ├── schemas/
  │   │   ├── events.py           # discriminated-union event schemas, versioned
  │   │   ├── leads.py
  │   │   └── responses.py
  │   ├── services/
  │   │   ├── ingest.py
  │   │   ├── resolve.py
  │   │   ├── interpret.py
  │   │   ├── score.py
  │   │   ├── act.py
  │   │   └── prove.py            # reconciliation logic
  │   ├── routers/
  │   │   ├── events.py
  │   │   ├── leads.py
  │   │   ├── manual_review.py
  │   │   ├── dashboard.py
  │   │   └── admin.py            # replay, simulate-failure
  │   ├── policies/
  │   │   ├── scoring_policy_v1.json
  │   │   └── identity_policy_v1.json
  │   ├── templates/               # Jinja2 dashboard HTML
  │   └── logging.py
  ├── fixtures/
  │   ├── web_form_events.json
  │   ├── social_mention_events.json
  │   ├── email_engagement_events.json
  │   └── generate_and_post.py     # posts fixtures against the running API
  ├── tests/
  │   ├── unit/
  │   └── integration/
  ├── docker-compose.yml
  ├── Dockerfile
  ├── .env.example
  ├── alembic.ini
  ├── requirements.txt
  ├── README.md
  ├── AI_USAGE.md
  └── ai-usage.json
```

**Database Schema**

| Table | Field | Type | Required | Notes |
|---|---|---|---|---|
| `events` | `id` | UUID (PK) | Y | |
| `events` | `external_event_id` | TEXT | Y | Source-provided ID |
| `events` | `source` | TEXT | Y | enum: `web_form`, `social_mention`, `email_engagement` |
| `events` | `dedupe_key` | TEXT (UNIQUE) | Y | hash(source + external_event_id) — DB-enforced |
| `events` | `payload_hash` | TEXT | Y | hash(canonicalized body); compared on a `dedupe_key` match to distinguish a true duplicate from an edit |
| `events` | `is_edit` | BOOLEAN | Y | default false; true when this row's content was updated via an edited resubmission |
| `events` | `schema_version` | TEXT | Y | |
| `events` | `campaign_id` | TEXT | N | |
| `events` | `identity_fields` | JSONB | Y | email/phone/name as submitted |
| `events` | `raw_payload` | JSONB | Y | full original body, for audit |
| `events` | `consent` | BOOLEAN | Y | |
| `events` | `received_at` | TIMESTAMPTZ | Y | |
| `events` | `is_valid` | BOOLEAN | Y | |
| `events` | `invalid_reason` | TEXT | N | |
| `events` | `created_at` | TIMESTAMPTZ | Y | default now() |
| `identities` | `id` | UUID (PK) | Y | canonical `identity_id` / `contact_id` |
| `identities` | `primary_email` | TEXT | N | |
| `identities` | `primary_phone` | TEXT | N | |
| `identities` | `display_name` | TEXT | N | |
| `identities` | `created_at` | TIMESTAMPTZ | Y | |
| `identity_links` | `id` | UUID (PK) | Y | |
| `identity_links` | `identity_id` | UUID (FK → identities.id) | Y | |
| `identity_links` | `event_id` | UUID (FK → events.id) | Y | |
| `identity_links` | `match_confidence` | NUMERIC(3,2) | Y | |
| `identity_links` | `match_rule` | TEXT | Y | `exact_email` / `exact_phone` / `fuzzy_name_company` |
| `manual_review_queue` | `id` | UUID (PK) | Y | |
| `manual_review_queue` | `event_id` | UUID (FK → events.id) | Y | |
| `manual_review_queue` | `candidate_identity_id` | UUID (FK, nullable) | N | |
| `manual_review_queue` | `reason` | TEXT | Y | |
| `manual_review_queue` | `status` | TEXT | Y | `pending` / `resolved` / `rejected` |
| `manual_review_queue` | `resolved_at` | TIMESTAMPTZ | N | |
| `interpretations` | `id` | UUID (PK) | Y | |
| `interpretations` | `event_id` | UUID (FK, UNIQUE) | Y | one interpretation per event |
| `interpretations` | `label` | TEXT | Y | or `"unknown"` |
| `interpretations` | `confidence` | NUMERIC(3,2) | Y | |
| `interpretations` | `reason` | TEXT | Y | |
| `interpretations` | `model_version` | TEXT | Y | |
| `interpretations` | `prompt_version` | TEXT | Y | |
| `scores` | `id` | UUID (PK) | Y | |
| `scores` | `event_id` | UUID (FK) | Y | |
| `scores` | `identity_id` | UUID (FK, nullable) | N | |
| `scores` | `score` | INTEGER | Y | 0–100 |
| `scores` | `features` | JSONB | Y | inputs that produced the score |
| `scores` | `policy_version` | TEXT | Y | |
| `scores` | `decision` | TEXT | Y | `hot`/`warm`/`cold`/`needs_review` |
| `leads` | `id` | UUID (PK) | Y | |
| `leads` | `identity_id` | UUID (FK, UNIQUE) | Y | idempotency anchor — one lead per identity |
| `leads` | `status` | TEXT | Y | `new`/`qualified`/`routed`/`escalated`/`closed` |
| `leads` | `source_event_id` | UUID (FK) | Y | event that created it |
| `leads` | `created_at` / `updated_at` | TIMESTAMPTZ | Y | |
| `routes` | `id` | UUID (PK) | Y | |
| `routes` | `lead_id` | UUID (FK) | Y | |
| `routes` | `queue` | TEXT | Y | |
| `routes` | `rule_matched` | TEXT | Y | |
| `routes` | `assigned_at` | TIMESTAMPTZ | Y | |
| `routes` | `sla_deadline` | TIMESTAMPTZ | Y | |
| `routes` | `escalated` | BOOLEAN | Y | default false |
| `attribution_touches` | `id` | UUID (PK) | Y | |
| `attribution_touches` | `identity_id` | UUID (FK) | Y | |
| `attribution_touches` | `event_id` | UUID (FK) | Y | |
| `attribution_touches` | `touch_type` | TEXT | Y | `first` / `last` / `intermediate` |
| `attribution_touches` | `source` | TEXT | Y | denormalized from the originating event, so attribution is directly inspectable without a join |
| `attribution_touches` | `campaign_id` | TEXT | N | denormalized from the originating event |
| `attribution_touches` | `occurred_at` | TIMESTAMPTZ | Y | |
| `receipts` | `id` | UUID (PK) | Y | |
| `receipts` | `action_type` | TEXT | Y | `lead_created`, `lead_updated`, `routed`, `escalated`, `review_queued`, `review_resolved`, `event_rejected`, `event_edited`, `dead_lettered` |
| `receipts` | `ref_table` / `ref_id` | TEXT / UUID | Y | what the action refers to |
| `receipts` | `result` | TEXT | Y | `success`/`failure` |
| `receipts` | `latency_ms` | INTEGER | Y | |
| `receipts` | `created_at` | TIMESTAMPTZ | Y | |
| `dead_letter_queue` | `id` | UUID (PK) | Y | |
| `dead_letter_queue` | `event_id` | UUID (FK) | Y | |
| `dead_letter_queue` | `stage` | TEXT | Y | `ingest`/`resolve`/`interpret`/`score`/`act` |
| `dead_letter_queue` | `error` | TEXT | Y | |
| `dead_letter_queue` | `retry_count` | INTEGER | Y | |
| `dead_letter_queue` | `resolved` | BOOLEAN | Y | default false |

**API Design**

| Method | Endpoint | Request Body | Response | Status Codes |
|--------|----------|---------------|----------|--------------|
| POST | `/api/v1/events` | Discriminated union by `source` (see Section 4 FR-1) | `{event_id, is_valid, invalid_reason?, duplicate?}` | 200, 400 |
| GET | `/api/v1/events/{event_id}` | — | Full event + pipeline state | 200, 404 |
| GET | `/api/v1/leads/{lead_id}` | — | Lead + route + attribution + score | 200, 404 |
| GET | `/api/v1/leads` | Query params: `status`, `source`, `decision` | List of leads | 200 |
| GET | `/api/v1/manual-review` | Query: `status=pending` | List of pending reviews | 200 |
| POST | `/api/v1/manual-review/{id}/resolve` | `{decision: "merge_into" \| "create_new", identity_id?}` | Updated review + resumed pipeline result | 200, 404, 409 |
| GET | `/api/v1/dashboard/summary` | Query: `since`, `until` | Counts by source/status/decision | 200 |
| GET | `/api/v1/dashboard/reconciliation` | Query: `since`, `until` | `{dashboard_count, receipt_count, variance, status}` | 200 |
| POST | `/api/v1/admin/replay/{event_id}` | — (requires `Authorization: Bearer <ADMIN_API_KEY>`) | Re-runs pipeline for a dead-lettered event | 200, 401, 404 |
| POST | `/api/v1/admin/simulate-failure` | `{stage, event_id}` (test harness only) | Forces a failure injection for test coverage | 200, 401 |
| GET | `/api/v1/dead-letter` | Query: `resolved=false` | List of DLQ entries | 200 |
| GET | `/health` | — | `{status: "ok", db: "ok"}` | 200, 503 |

**Third-Party Integrations**

| Service | Purpose | Auth Method |
|---------|---------|-------------|
| OpenRouter API (Claude model, routed) | Interpretation stage — pain/topic/intent classification | `OPENROUTER_API_KEY` env var; use a cheap/fast model, `temperature=0`, small max_tokens, pinned model string recorded per result |

No other third-party integrations are in scope — the three "signal sources" are internal fixture generators (`fixtures/generate_and_post.py`) that POST synthetic, DAXVORA-shaped payloads to the app's own `/api/v1/events` endpoint. They must be clearly labeled as **SIMULATED** connectors everywhere they appear in docs/demo, per the assessment brief's LIVE/TEST/MOCKED/SIMULATED labeling requirement.

**Labeling note:** the classification call in the interpretation stage is a real, live call to an external provider (Claude model, accessed via OpenRouter) — it must be labeled **LIVE** in the README/demo, explicitly distinct from the SIMULATED connectors, so the two are never conflated. Example: "Connectors: SIMULATED. Classification: LIVE call via OpenRouter."

**Environment / Deployment**
- Where it runs: Local, via `docker compose up` (one service for the FastAPI app, one for Postgres). No cloud deployment required for this project.
- Required environment variables:
  - `DATABASE_URL` — Postgres connection string
  - `OPENROUTER_API_KEY` — for the interpretation stage (routed through OpenRouter's OpenAI-compatible endpoint)
  - `CLASSIFICATION_MODEL` — pinned OpenRouter model string, e.g. `anthropic/claude-haiku-4.5` (exact identifier to be confirmed against OpenRouter's current model list at implementation time; cheap/fast is appropriate here; document the choice and cost in the required cost-and-limits section of the README)
  - `ADMIN_API_KEY` — static bearer token for `/api/v1/admin/*` endpoints
  - `SCORING_POLICY_VERSION` / `IDENTITY_POLICY_VERSION` — which versioned policy file is active
  - `LOG_LEVEL` — default `INFO`
  - `APP_ENV` — `local` / `test`
  - `RETRY_MAX_ATTEMPTS` (default 3), `RETRY_BASE_DELAY_MS` (default 500)

---

## 7. Data Requirements

- **What data is stored & where:** All event, identity, scoring, routing, attribution, and receipt data lives in PostgreSQL. Nothing is stored in external services. Raw synthetic PII (name/email/phone in fixture data) is stored as-is in the DB (needed for identity resolution logic) but must never be synthetic-*real* — use clearly fake data (e.g., `@example.com` addresses) per the assessment's "no client/private data" rule.
- **Retention rules:** Events and receipts are retained indefinitely for audit purposes within this assessment's scope (no deletion/TTL logic required for v1). Note this explicitly as a v1 limitation, not an oversight.
- **Sample/seed data format:** JSON fixture files under `fixtures/`, one per source type, each containing an array of event objects matching that source's schema — including deliberately duplicate, edited, malformed, and edge-case entries to drive the required test pack (see Section 11).
- **Privacy/compliance needs:** No real client or personal data at any point (assessment rule). Logs must redact/mask PII fields (hash or truncate email/phone before writing to structured logs). `consent` field is captured per event but no consent-management workflow (e.g., unsubscribe) is in scope for v1.

---

## 8. UI/UX Specifications

**Key Screens**

| Screen | Purpose | Key Components |
|--------|---------|-----------------|
| Dashboard summary | Prove correctness at a glance | Counts by source, status, decision; reconciliation pass/fail badge; link to dead-letter list |
| Lead detail (simple) | Let evaluator inspect one lead's full trail | Lead status, linked events, score/decision with `features`, route + `rule_matched`, attribution history |
| Manual review queue | Let evaluator resolve ambiguous matches | Pending reviews with reason, candidate identity, resolve action |
| Dead-letter list | Show failure handling working | Stage, error, retry count, replay action |

**Design System**
No brand kit applies here (that's Project 03's DAXVORA site). Plain, semantic HTML via Jinja2 templates; system font stack; minimal CSS (a single small stylesheet is fine). Legibility and correctness over polish — this is evaluator tooling.

**Responsive Behavior**
Not a scored requirement for this project. Reasonable desktop-width layout is sufficient; no mobile optimization needed.

**Wireframes/Mockups**
None provided — layout is left to implementation discretion given the "Key Screens" table above.

---

## 9. Constraints & Assumptions

**Constraints**
- Budget: $0 mandatory — this is an unpaid assessment; no paid service, tier, or usage may be incurred without written approval from the evaluator contact (Krishnam), per the assessment's shared rules. Written approval for LIVE API usage in the interpretation stage has been obtained from Krishnam. The only real spend risk is OpenRouter API token usage for the interpretation stage — keep it small (short prompts, cheap model, skip-LLM-below-min-length rule in FR-4) and disclose actual token cost in the README's cost section.
- Timeline: Set by the candidate's own estimate as required by the assessment brief — not fixed by this PRD.
- Other: Must be independently runnable and explainable by the candidate (assessment's "own words" requirement) — avoid opaque, unexplainable generated code.

**Assumptions**
- Single evaluator, single environment — no concurrent multi-user access patterns need to be load-tested, though the DB-level uniqueness constraints must still hold under concurrent requests (tested via the duplicate/replay test, which should include a concurrency variant — see Section 11).
- Fixture data stands in for real connector data; no live Reddit/social/email API access is being sought for this project (that's a Project 01 concern for Reddit specifically).
- Evaluator will run everything locally via Docker; no public hosting is expected for this project.

**Known Limitations Accepted for v1**
- No real third-party connector integrations — synthetic fixtures only, clearly labeled SIMULATED.
- No message broker — in-database dead-letter table only.
- No multi-tenant, multi-user auth, or RBAC.
- No data retention/deletion policy implemented.
- Dashboard is functional, not branded/polished.

---

## 10. Acceptance Criteria

| Feature | Acceptance Criteria |
|---------|----------------------|
| Schema validation | This feature is done when invalid events across all three sources are isolated with a specific `invalid_reason` and valid events receive a stable `event_id`, verified by a test asserting both paths for every source type. |
| Duplicate/replay handling | Done when replaying any valid event (including under simulated concurrent requests) never creates a second `leads`, `routes`, or `receipts` row — verified by a test that submits the same event N times and asserts row counts stay at 1. |
| Edited resubmission | Done when a resubmission with the same `external_event_id` but a different `payload_hash` updates the existing event (`is_edit=true`), re-runs the pipeline, writes an `event_edited` receipt, and is distinguishable in test assertions from the exact-duplicate no-op case above — verified by a test that submits an edit and confirms both the updated content and the receipt, with no second `leads` row created. |
| Identity resolution | Done when exact email/phone matches auto-link, fuzzy matches always land in `manual_review_queue` (never auto-merged), and both paths are covered by seeded fixture cases. |
| Classification | Done when every valid, resolved event has a `label`+`confidence`+`reason` or an explicit `unknown`, including a fixture case with deliberately sparse text. |
| Scoring | Done when `score`, `features`, `policy_version`, and `decision` are all present and reproducible — re-running the same event through the pipeline (in a test DB) yields the same score. |
| Routing | Done when every route records `rule_matched`, and a fixture case with no matching rule correctly falls back to the default queue. |
| Attribution | Done when first-touch is immutable and last-touch updates correctly across a multi-event sequence for one identity, verified with an out-of-order delivery test case. |
| Failure recovery | Done when a simulated provider failure (via `/api/v1/admin/simulate-failure`) results in bounded retries, then a dead-letter entry, and a subsequent replay succeeds without side effects from the failed attempt. |
| Reconciliation | Done when `GET /api/v1/dashboard/reconciliation` reports `variance: 0` after running the full seeded pack including duplicates and forced failures. |
| Privacy | Done when a log inspection (part of the required test report) shows no raw email/phone strings appear in structured logs. |

---

## 11. Testing Requirements

- **Must have unit tests:** Event schema validation (all 3 sources, valid + invalid cases), `dedupe_key` computation, identity-matching rule functions (exact + fuzzy, at and around the confidence threshold), scoring policy application (including tie-breaking and insufficient-data cases), routing rule evaluation (including fallback).
- **Must have integration tests (against a real test Postgres, not mocks):** Full pipeline happy path per source type; duplicate/replay (including a concurrency variant — fire the same event twice near-simultaneously and assert only one lead exists); edited resubmission (same `external_event_id`, changed payload) confirming updated content, re-run pipeline effects, and an `event_edited` receipt distinct from the duplicate no-op; ambiguous-identity → manual-review → resolve → pipeline resumes, asserting a `review_resolved` receipt is written in addition to the original `review_queued` receipt; simulated provider failure → retry → dead-letter → replay; multi-event attribution sequence including out-of-order arrival, asserting `source`/`campaign_id` on each `attribution_touches` row match the originating event; reconciliation endpoint against a known seeded dataset with a hand-computed expected variance of 0.
- **Critical paths that must be tested before "done":** Everything in Section 10's Acceptance Criteria table, plus a full clean-environment run (`docker compose up` + seed + test suite) to confirm reproducibility as required by the assessment's "Before you send" checklist.

---

## 12. Open Questions

- Final choice of Claude model (accessed via OpenRouter) for the interpretation stage (balance of cost vs. classification quality) — default to the fastest/cheapest current model unless testing shows it's not reliable enough; document whichever is chosen and why in the README.
- Exact fuzzy-matching algorithm/library for the `fuzzy_name_company` identity rule (e.g., simple normalized Levenshtein/Jaro-Winkler threshold vs. a small library) — left to implementation, but the chosen method and its threshold must be written into `identity_policy_v1.json`, not buried in code.
- Whether SLA/escalation actually needs a background scheduler (to flip `escalated=true` when `sla_deadline` passes) or whether it's computed on-read (`sla_deadline < now()`) for v1 — recommend **on-read** to avoid adding a scheduler dependency, but flag this as a deliberate simplification in the plan-vs-built writeup.

---

## Appendix: AI Build Instructions

- **Coding style/conventions to follow:** PEP 8, full type hints everywhere (`mypy`-clean if feasible), `async`/`await` throughout the request path (no blocking calls inside async handlers — including the OpenRouter call, use the `openai` SDK's async client pointed at OpenRouter's base URL), Pydantic models for every request/response body (no raw dicts crossing a boundary).
- **Preferred error-handling pattern:** Centralized FastAPI exception handlers producing a consistent JSON error envelope (`{"error": "<code>", "detail": "..."}`); domain logic raises typed exceptions (e.g., `InvalidEventError`, `AmbiguousIdentityError`) rather than returning sentinel values; never swallow an exception silently — every caught error either retries (per FR-11), writes a receipt/dead-letter row, or re-raises. The OpenRouter call (via the `openai` async client pointed at OpenRouter's base URL) follows the same rule — no blocking calls inside async handlers.
- **Preferred state-management approach:** N/A — backend-only project, no frontend state to manage. The Jinja2 dashboard is read-only/stateless per request.
- **Naming conventions:** `snake_case` for Python files, functions, variables, and DB columns; table names plural `snake_case` (as shown in Section 6); Pydantic schema classes `PascalCase` suffixed by purpose (e.g., `WebFormEventIn`, `LeadOut`).
- **Anything the AI should NOT do:**
  - Do not call any real Reddit, social, or email provider API from this project — all three connectors are internal fixture generators only, and must be labeled SIMULATED in every README/demo reference.
  - Do not auto-merge any identity match below the configured confidence threshold, under any circumstance, even to make a demo look cleaner.
  - Do not fabricate a nonzero score for `label="unknown"` — insufficient data must resolve to `needs_review`.
  - Do not log raw PII (email, phone, full name) anywhere in structured logs or in files intended for the evidence export.
  - Do not skip writing a `receipts` row for any mutating action — the reconciliation check depends on this being exhaustive.
  - Do not introduce a message broker, cloud queue, or paid service without explicit written approval (per the assessment's shared rules) — the in-DB outbox/dead-letter pattern is the intended v1 solution, not a placeholder to be "fixed" unprompted.
  - Do not silently drop an invalid event — always persist it with `is_valid=false` and a reason.
  - This PRD is the single source of truth for this project's build — if something needed to implement a task isn't covered here or in the original assessment brief, flag it as an open question (Section 12) rather than inventing an assumption silently.
