# Project 02 — Demand-Signal Workflow: Phased Implementation Plan
**Evaluation ID:** DAXVORA-RAJAT-2026-08-A01
**Companion to:** `PRD_Demand_Signal_Workflow_v1_2.md`
**Version:** v1.0

---

## How to use this plan

- Each phase has an **objective**, **tasks** (mapped to specific PRD sections/FRs), **deliverables**, and a **Phase Gate** — a checklist you run before moving on.
- A Phase Gate has three lanes you check every time, because they're scored/gated independently:
  1. **PRD alignment** — does the code match the FR/table/flow it implements?
  2. **Assessment brief alignment** — does it satisfy the original per-project acceptance checks and the cross-project delivery standard (README, tests, observability, demo, evidence, plan-vs-built, cost/limits)?
  3. **Compliance checklist** — LIVE/TEST/MOCKED/SIMULATED labeled at the point of use, no secrets committed, `AI_USAGE.md`/`ai-usage.json` updated for *this phase's* work (not deferred), automatic-failure gates re-checked.
- Do not start a phase's tests in Phase 10 only — write the phase's unit tests as you build it. Phase 10 is for integration tests that need the whole pipeline wired up, plus the full acceptance-criteria sweep.
- Log AI usage (model, prompts, what was generated vs. hand-edited, verification performed) **at the end of every phase**, not at the end of the week. This is the checklist's explicit warning, and it's an automatic-failure gate if reconstructed from memory later and it shows.
- **Explainability checkpoint, every phase, non-negotiable:** the brief's automatic-failure gates include "candidate cannot explain the architecture, important code paths, agent logic, limitations, or AI-generated work in their own words." Since an AI agent is writing the code, this only holds up if you personally read and can restate what each phase does before approving it — not just check that its tests pass. Build this into your workflow now, not at submission time.
- **Standing constraints, all phases:** never call a real Reddit, social platform, or email/marketing API — the three signal sources are internal fixture generators only, always. Never introduce a message broker or other paid infrastructure component beyond the approved OpenRouter usage — the in-DB outbox/dead-letter table is the intended v1 design, not a placeholder.

---

## Phase 0 — Scaffolding, Environment, and Disclosure Setup

**Objective:** Nothing feature-related yet. Get the skeleton, the DB, and the two "don't retrofit this" systems (observability, AI disclosure) running first.

**Tasks**
- Repo init, folder structure exactly as in PRD §6 (Folder/File Structure).
- `docker-compose.yml` (app + Postgres), `Dockerfile`, `alembic.ini`, `requirements.txt` pinned per PRD §6 Tech Stack.
- `.env.example` with **names only** — `DATABASE_URL`, `OPENROUTER_API_KEY`, `CLASSIFICATION_MODEL`, `ADMIN_API_KEY`, `SCORING_POLICY_VERSION`, `IDENTITY_POLICY_VERSION`, `LOG_LEVEL`, `APP_ENV`, `RETRY_MAX_ATTEMPTS`, `RETRY_BASE_DELAY_MS`.
- `app/config.py` via `pydantic-settings` reading `.env`.
- `structlog` configured for JSON-line output before any endpoint exists — this is the observability backbone every later phase writes into.
- Empty `AI_USAGE.md` and `ai-usage.json` scaffolded with the required fields (per compliance checklist §2) and the `evaluation_id` pre-filled. Start logging from your very first AI-assisted commit.
- Confirm `.gitignore` excludes `.env`, and do a manual scan of the initial commit for anything that looks like a secret before pushing.

**Deliverables:** running `docker compose up` with an empty FastAPI app responding on `/health`; `.env.example`; disclosure files scaffolded.

**Phase Gate**
- [ ] `docker compose up` succeeds clean, `/health` returns `{status: "ok", db: "ok"}`.
- [ ] No `.env` (with values) anywhere in git history, only `.env.example`.
- [ ] `AI_USAGE.md` / `ai-usage.json` exist and have an entry for this phase already, not blank.
- [ ] Folder structure matches PRD §6 exactly (deviations noted, not silent).

---

## Phase 1 — Ingest: Schema, Validation, Dedupe/Edit Detection

**Maps to:** FR-1, FR-2; `events` table; Flow 1 step 1–2; Flow 2 (all); acceptance table rows "Schema validation," "Duplicate/replay handling," "Edited resubmission."

**Sub-phase 1a — Schema**
- Discriminated-union Pydantic schemas per source (`web_form`, `social_mention`, `email_engagement`) in `app/schemas/events.py`, versioned (`schema_version`).
- `POST /api/v1/events`: invalid payload → persist with `is_valid=false` + `invalid_reason`, return 200 (per PRD Error States table — this is *not* a 400, only malformed JSON is).
- Malformed JSON (not schema-conformant) → 400, not persisted (no valid schema to isolate).

**Sub-phase 1b — Dedupe & edit detection**
- `dedupe_key = hash(source + external_event_id)`, DB-level unique constraint (not just app logic — protects against race conditions per FR-2).
- `payload_hash = hash(canonicalized body)`.
- Same `dedupe_key` + same `payload_hash` → true duplicate, no-op, `duplicate: true`, no new rows anywhere.
- Same `dedupe_key` + different `payload_hash` → edit: update existing `events` row, `is_edit=true`, write `event_edited` receipt (receipts table comes in Phase 6, but the write-path contract should exist as a stub now).

**Deliverables:** `/api/v1/events` live; `events` + unique constraint in place; unit tests for schema validation (all 3 sources, valid/invalid) and `dedupe_key`/`payload_hash` computation.

**Phase Gate**
- [ ] **PRD:** FR-1 and FR-2 error/response shapes match the PRD's Error States table exactly (status codes and body shapes, not close-enough).
- [ ] **Brief:** "malformed input" and "duplicate/retry" acceptance checks (assessment §Project 02 acceptance table) both pass against fixture cases.
- [ ] **Compliance:** every event source is labeled SIMULATED in whatever demo/README stub you've started — don't wait until Phase 11 to write this down, note it now while it's fresh.
- [ ] Concurrency check: fire the same event twice near-simultaneously (even a crude asyncio test) and confirm the DB unique constraint — not application logic — is what prevents the second row.

---

## Phase 2 — Resolve: Identity Resolution & Manual Review

**Maps to:** FR-3; `identities`, `identity_links`, `manual_review_queue` tables; Flow 3; acceptance table row "Identity resolution."

**Tasks**
- Rule order: exact email (high confidence, auto-link) → exact normalized phone (high confidence, auto-link) → fuzzy name+company (low confidence, **manual review only, never auto-merge**).
- Confidence threshold (default 0.85) lives in `identity_policy_v1.json`, not hardcoded — pick and document the fuzzy-matching library/algorithm here (this was an open question in PRD §12 — resolve it now, write the decision into the policy file and the README's later "Plan vs Built" notes).
- Below threshold → `manual_review_queue` row with reason, pipeline halts for that event only (interpret/score/act do not run yet).
- `POST /api/v1/manual-review/{id}/resolve` → merge_into or create_new, resumes pipeline.

**Phase Gate**
- [ ] **PRD:** No code path can auto-merge below threshold "to make a demo look cleaner" (explicit Appendix instruction) — write a test that tries to force this and confirms it's rejected.
- [ ] **Brief:** ambiguous-identity fixture cases correctly land in manual review, not force-merged.
- [ ] **Compliance:** confidence threshold and matching rule are visible/inspectable (not buried), satisfying the "explainability" rule — you should be able to point at `identity_policy_v1.json` and explain the number.
- [ ] Test the exact confidence boundary (0.849 vs 0.85), per PRD §11's explicit requirement to test "at and around the threshold."

---

## Phase 3 — Interpret: LIVE Classification via OpenRouter

**Maps to:** FR-4; `interpretations` table; Flow 1 step 4; Flow 4 (failure path); acceptance table row "Classification."

**Tasks**
- Async `openai` SDK client pointed at OpenRouter's base URL, `OPENROUTER_API_KEY` from env, `CLASSIFICATION_MODEL` pinned.
- Text under configured min length (default 8 tokens) → `label="unknown"` **without calling the LLM** — this is a scored efficiency behavior (assessment §Agent Efficiency, "resource efficiency"), not just a cost nicety, so make sure the test actually asserts the LLM was *not* called for these cases, not just that the output is `unknown`.
- `temperature=0`, small `max_tokens`, record `model_version` + `prompt_version` on every result.
- Wire this into the retry/dead-letter path from FR-11 now (even a minimal version) — this is the one external call in the whole system, so it's the primary target for the "provider failure" test category from the assessment's evaluator test pack.

**Phase Gate**
- [ ] **PRD:** Confirm this call is genuinely LIVE (hits OpenRouter, real response) — do not mock it and label it live; do not accidentally leave a placeholder response wired in.
- [ ] **Brief:** run a real provider-failure simulation (bad key, or `simulate-failure` stub) and confirm bounded retry → dead-letter, not an infinite loop or a silent `unknown`.
- [ ] **Compliance:** this is the one spot in the whole build where the LIVE label matters most — confirm the demo/UI/logs distinguish this from the SIMULATED connectors, per the exact wording pattern in the PRD ("Connectors: SIMULATED. Classification: LIVE call via OpenRouter.").
- [ ] Log actual token usage from a real test run into the README's cost section — not an estimate, a measured number, once you've run the fixture pack once.

---

## Phase 4 — Score: Versioned Policy Application

**Maps to:** FR-5; `scores` table; acceptance table row "Scoring."

**Tasks**
- `scoring_policy_v1.json`: features, weights/thresholds, tie-break rule, insufficient-data rule.
- `label="unknown"` → **must** resolve to `decision="needs_review"`, never a fabricated numeric score (explicit Appendix "do not" instruction and an automatic-failure-adjacent risk if violated).
- Re-running the same event through the pipeline in a test DB must yield the identical score (determinism requirement).

**Phase Gate**
- [ ] **PRD:** determinism test passes — same input, same output, twice.
- [ ] **Brief:** score/features/policy_version/decision are all present and traceable per the assessment's "explainable worth-replying"-style requirement applied here to lead scoring.
- [ ] **Compliance:** no fabricated certainty — this directly maps to the brief's "unsupported certainty" failure pattern, applied to leads instead of Reddit advice.

---

## Phase 5 — Act: Lead Creation, Routing, SLA

**Maps to:** FR-6, FR-7; `leads`, `routes` tables; Flow 1 step 6; acceptance table row "Routing."

**Tasks**
- `identity_id` unique constraint on `leads` as the idempotency anchor.
- Lead create/update + receipt write in a **single transaction** (both succeed or both roll back) — this is explicit in FR-6, don't split it across two commits.
- Explicit ordered routing rule table, fallback queue if nothing matches, `rule_matched` recorded on every route (not just successful custom-rule matches).

**Phase Gate**
- [ ] **PRD:** the fallback-queue fixture case (no matching rule) actually falls back rather than erroring or silently dropping.
- [ ] **Brief:** "exactly once" is verified by an actual row-count assertion test, not eyeballing.
- [ ] **Compliance:** transaction atomicity — kill the process mid-write in a test and confirm you never get a lead without a receipt or vice versa.

---

## Phase 6 — Attribution

**Maps to:** FR-8; `attribution_touches` table; acceptance table row "Attribution."

**Tasks**
- First-touch immutable once set; last-touch = most recent valid non-duplicate event by `received_at`, ties by `event_id` insertion order.
- Denormalize `source`/`campaign_id` onto the touch row.
- An edit (Phase 1's edit path) updates the existing touch's denormalized fields in place, not a new touch row.

**Phase Gate**
- [ ] **PRD:** out-of-order delivery test — send an "earlier" event after a "later" one has already landed, confirm attribution still resolves correctly by `received_at`, not arrival order.
- [ ] **Brief:** attribution "survives retries and conflicts under the stated policy" — confirmed by test, not assumption.
- [ ] **Compliance:** nothing here touches PII beyond what's already redacted in logs (Phase 7) — sanity check now.

---

## Phase 7 — Prove: Receipts, Reconciliation, Redacted Logging

**Maps to:** FR-9, FR-10; `receipts` table; Flow 5; acceptance table rows "Failure recovery," "Reconciliation," "Privacy."

**Tasks**
- Every mutating action writes a `receipts` row — this list is explicit in FR-9, treat it as exhaustive: `event_rejected`, `event_edited`, `lead_created`, `lead_updated`, `routed`, `escalated`, `review_queued`, `review_resolved`, `dead_lettered`.
- `GET /api/v1/dashboard/reconciliation` independently recomputes totals from `receipts` and diffs against dashboard counts.
- `structlog` output includes input ID, decision, reason, action, result, error, timing — this is the assessment's exact observability requirement (compliance checklist §1), don't approximate it.
- PII redaction in logs: hash or mask email/phone before writing to structured logs — raw PII may live in the DB (needed for identity resolution) but never in logs or the evidence export.

**Phase Gate**
- [ ] **PRD:** `variance: 0` on a full seeded run including duplicates and forced failures — this is a hard pass/fail per FR-10, not a "close enough."
- [ ] **Brief:** observability log sample actually contains all seven required fields (input ID, decision, reason, action, result, error, timing) — grep for them, don't assume.
- [ ] **Compliance:** run a log-inspection pass specifically for raw email/phone strings leaking through — this is a named acceptance test ("Privacy" row) and a named compliance rule; do this before you consider Phase 7 done, not at final packaging.

---

## Phase 8 — Fail Safely: Retry, Dead-Letter, Replay

**Maps to:** FR-11; `dead_letter_queue` table; Flow 4; acceptance table row "Failure recovery."

**Tasks**
- `tenacity`-based bounded retry (max 3, base 500ms, jitter) wrapping the OpenRouter call specifically (the one external dependency).
- On exhaustion → `dead_letter_queue` row with stage, error, retry_count.
- `POST /api/v1/admin/replay/{event_id}` (bearer-token gated via `ADMIN_API_KEY`) — replay must be idempotent even if an earlier stage partially succeeded.
- `POST /api/v1/admin/simulate-failure` test harness endpoint for forcing failure injection in tests.

**Phase Gate**
- [ ] **PRD:** replay after a partial-success failure doesn't double-write anything downstream (re-run the Phase 5/6 idempotency tests through the replay path specifically, not just the happy path).
- [ ] **Brief:** this directly satisfies the cross-project "provider failure" test requirement — confirm with a real forced 429/timeout/outage simulation, not just a code review.
- [ ] **Compliance:** admin endpoints actually reject an unauthenticated/wrong-token request (401) — test this, since it's your only auth gate.

---

## Phase 9 — Dashboard (Evaluator Tooling)

**Maps to:** PRD §8 (UI/UX Specifications); Flow 5.

**Tasks**
- Server-rendered Jinja2, plain semantic HTML, no JS framework/build step (per PRD — explicitly not the scored/branded surface, that's Project 03).
- Four screens: dashboard summary, lead detail, manual review queue, dead-letter list — as specified in PRD §8.

**Phase Gate**
- [ ] **PRD:** reconciliation pass/fail badge is visibly correct against the live `/reconciliation` endpoint, not a hardcoded placeholder.
- [ ] **Brief:** not scored on responsiveness/accessibility for this project (correctly out of scope per PRD §5) — don't over-invest here at the expense of Phase 10/11 time.
- [ ] **Compliance:** every count/state shown traces back to a receipt — this is the dashboard's whole reason for existing.

---

## Phase 10 — Full Test Sweep (Integration)

**Maps to:** PRD §11 in full; assessment's cross-project delivery standard "Tests" row; evaluator test pack (assessment §13).

**Tasks — run against a real test Postgres, not mocks**
- Full happy path per source type.
- Duplicate/replay including the concurrency variant.
- Edited resubmission (distinct receipt/behavior from exact duplicate).
- Ambiguous identity → manual review → resolve → pipeline resumes (`review_resolved` receipt present, in addition to `review_queued`).
- Simulated provider failure → retry → dead-letter → replay.
- Multi-event attribution sequence including out-of-order arrival.
- Reconciliation against a known seeded dataset with a hand-computed expected variance of 0.
- Full clean-environment run: `docker compose up` + seed + test suite, timed (PRD's <5 minute setup target).
- **Performance/latency tests against PRD §5's NFR targets specifically** — these live outside the §10 acceptance-criteria table and are easy to skip: single event ingest→act (including the real LLM call, excluding manual-review pauses) under 3 seconds under seeded load; `/dashboard/summary` and `/dashboard/reconciliation` each under 1 second for up to 500 seeded events. The assessment's Efficiency scoring rubric grades "Speed: meets the candidate's declared latency/processing target" — this is the test that proves it, with real measured numbers, not an assumption that it's probably fine.

**Phase Gate**
- [ ] **PRD:** every row in PRD §10 Acceptance Criteria table has a corresponding passing test — go down the table literally, check each one off.
- [ ] **PRD:** §5 NFR performance targets (3s per event, 1s dashboard/reconciliation) verified by a real timed test, not assumed.
- [ ] **Brief:** every case group in the evaluator test pack (§13) that applies to this project — clear positives/negatives, ambiguous, duplicates & edits, provider failure — has a corresponding fixture.
- [ ] **Compliance:** this is your last chance to catch an automatic-failure condition before documentation — re-read the compliance checklist's §4 list line by line against what you actually built.

---

## Phase 11 — Documentation & Disclosure Finalization

**Maps to:** cross-project delivery standard (README, architecture view, build evidence, plan-vs-built, cost & limits); compliance checklist §2 and §5.

**Tasks**
- README: purpose, architecture, setup, config/env vars, usage, **known limitations**, troubleshooting.
- Architecture diagram: inputs → decisions → agents/automations → storage → actions → failure paths — simple, not decorative (explicit instruction in both source docs).
- Cost & limits section: actual measured OpenRouter token cost from a real seeded run (not an estimate), rate limits, free-tier assumptions if any, explicit statement that no paid service was used beyond the approved OpenRouter usage.
- Plan vs. built: what changed from the PRD (e.g., the OpenRouter switch itself is a good real example to document here), known gaps, what you'd do with more time.
- Finalize `AI_USAGE.md` / `ai-usage.json` — this should mostly be a compilation of what you logged incrementally per phase, not a from-scratch reconstruction. If it reads like a reconstruction, that's the exact pattern the compliance checklist warns is a failure signal.
- Asset provenance: N/A for this project unless you used any external icon/font/dataset beyond your own fixtures — confirm and note "none" explicitly if so, don't leave it silently blank.

**Phase Gate**
- [ ] **PRD:** README's cost section has real numbers, not "approximately."
- [ ] **Brief:** architecture diagram literally shows all six required elements (inputs, decisions, agents/automations, storage, actions, failure paths) — check it against the list, not just "looks like a diagram."
- [ ] **Compliance:** `ai-usage.json` has all required fields per the checklist's field list (`evaluation_id`, `project`, `session_id`, `started_at`, `ended_at`, `provider`, `model`, `version`, `prompts_or_hashes`, `export_path`, `skills_tools`, `generated_files`, `human_modified_files`, `verification`, `costs`, `notes`) — validate the JSON against this list literally.

---

## Phase 12 — Final Verification & Submission Packaging

**Maps to:** assessment "Before you send" checklist; compliance checklist §5.

**Tasks**
- Run every documented command from a genuinely clean environment (fresh clone or fresh container), not your dev machine with cached state.
- Re-verify every LIVE/TEST/MOCKED/SIMULATED label is present at the point of use, not just in the README.
- Grep the whole repo + evidence export one more time for secrets, tokens, and any real PII.
- Confirm exact commit/version ID to cite in the submission.
- Record the demo (recording + local walkthrough using seeded cases) with environment state visibly labeled throughout, and write the short transcript/scenario list.
- Compile the final package per the assessment's Section 14 "Include" list.

**Phase Gate**
- [ ] **PRD:** clean-environment run completes in under 5 minutes with no manual steps beyond `.env.example` → `.env`.
- [ ] **Brief:** every item in the assessment's "Before you send" list is individually checked, not assumed from earlier phases still holding.
- [ ] **Compliance:** final automatic-failure gate re-read, one more time, against the finished artifact — this is the last checkpoint before it becomes unrecoverable (submitted).

---

## Open item

You flagged wanting to fold in "things mentioned in the original document" but the list cut off after "like -". If there's a specific item from the original assessment brief you want explicitly called out as its own phase-gate line (rather than folded into the relevant phase as I've done above), let me know and I'll add it in.
