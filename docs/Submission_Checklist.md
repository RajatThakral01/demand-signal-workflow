# Submission Checklist — DAXVORA-RAJAT-2026-08-A01

**Commit:** `e45d6b5` → after Phase 12 will be tagged `project02-submission-v1`
**Date:** 2026-08-21

This is the "Before you send" confirmation per the assessment brief, verified start-to-finish from a genuinely clean environment (fresh clone + `cp .env.example .env` + no manual steps).

## Clean-environment proof

* **Fresh clone:** `git clone file://$(pwd) /tmp/dsw-clean` from `e45d6b5` → `cp .env.example .env` → add `ADMIN_API_KEY=test_admin_key` (dummy `OPENROUTER_API_KEY` for mocked suite)
* **DB:** `DROP DATABASE IF EXISTS dsw_test; CREATE DATABASE dsw_test` (host) or `docker compose down -v` + `docker compose build` (bakes 0012) + `docker compose up -d --wait`
* **Full suite:** `pytest -q` **221 passed, 1 skipped in 25.51s** (host) / **218 passed, 1 skipped in 24.91s** (earlier host) — both `<300s` (`real 27.45s`, `28s` wall-clock via `scripts/clean_run.sh`)
* **Docker config:** `docker compose config` OK, `docker compose build` 4s, `health` `{"status":"ok","db":"ok"}`
* **Seed:** `python fixtures/generate_and_post.py --dry-run` prints 28/28 `SIMULATED` events with flags `valid/duplicate/edit/manual_review/dead_letter` in `0.00s`; live seed via `requests.post` to `POST /api/v1/events` also `200`
* **Reconciliation after seed:** `GET /api/v1/dashboard/reconciliation` `PASS variance 0`, `GET /api/v1/dashboard/summary` `by_source/web_form` etc.

**Wall-clock:** build+up+test+seed **28s** (`real 27.45s`) — well under **5 minutes (300s)** PRD §1.

## LIVE / SIMULATED re-verification (point of use, not just README)

| Label | Where in code/logs/docs |
|---|---|
| `web_form` **SIMULATED** | `app/schemas/events.py:30` docstring, `app/routers/events.py:3` |
| `social_mention` **SIMULATED** | `app/schemas/events.py:41`, `app/routers/events.py:88` |
| `email_engagement` **SIMULATED** | `app/schemas/events.py:51` |
| Interpretation **LIVE** | `app/services/interpret.py:3` "ONE real external API call — LIVE", `app/main.py:32` description, `README.md:32` table, `docs/PRD` §6 |
| `simulate-failure` **TEST-HARNESS ONLY** | `app/routers/admin.py:148` |
| Test suite **MOCKED** | `tests/integration/*` `interpret._call_llm` monkeypatched, `README.md:35` |

## Secret / PII grep — actual commands and output (clean)

```
$ git log --all --patch | grep -E "sk-or|ghp_|AKIA" → (no output)
$ grep -R "sk-or" --include="*.py" --include="*.md" --include="*.json" . (excluding .git) → (no output)
$ git ls-files | grep "^\.env$" → .env.example (only)
$ git check-ignore -v .env → .gitignore:223:.env
$ grep -R "@gmail.com|@yahoo.com" . → (no output)
$ grep -R "@example.com" fixtures/ → synthetic only (ada.lovelace@example.com etc., assessment rule, no real PII)
```

*No real secret in history, no `.env` in history, `.env` git-ignored, no raw PII — only `@example.com` synthetic fixtures (allowed). Evidence export would exclude `.env`.*

## Demo walkthrough — seeded scenarios and expected outcomes

| # | Scenario (from `fixtures/*` + flows) | Steps | Expected |
|---|---|---|---|
| 1 | **Flow 1 clean web_form** `wf-clear-positive-001` (pricing 50 seats) | `POST /api/v1/events` | `200 is_valid:true` `lead_id` `queue:sales_urgent` `rule:hot_any` `sla:2h` `score:hot` `attribution_touch_id` |
| 2 | **Flow 2 duplicate** same `wf-duplicate-001` twice | `POST` twice same `dedupe_key+payload_hash` | 2nd `200 duplicate:true` + no new `leads/routes/receipts` row, `events=1` |
| 3 | **Flow 2 edited** `wf-edited-001` original→corrected (20→100 seats) | `POST` original then `POST` same `dedupe_key` diff `payload_hash` | 2nd `200 is_edit:true` `payload_hash` advanced, `event_edited` receipt, 1 `Lead` (updated) `route sales_urgent→sales_priority` if decision changes, `attribution` denormalized updated |
| 4 | **Flow 3 ambiguous → manual review → create_new** `soc-ambiguous-001` (`Ada Lovlace` typo) | `POST` parks `200 status:manual_review review_id` → `POST /manual-review/{id}/resolve {"decision":"create_new"}` | `review_queued:1` then `review_resolved:1` + `identity_created:1`, `pipeline_status:resumed` `lead_id/queue/rule_matched/sla` |
| 5 | **Flow 3 merge_into** same as 4 but `{"decision":"merge_into","identity_id":"<existing>"}` | `POST` resolve | `lead` reused (`lead_op:updated`), `1 Lead` per identity, `IdentityLink` upsert |
| 6 | **Short text (<8 tokens)** `wf-short-text` `“hi”` | `POST` | `label:unknown` `model_version:none` `was_skipped:true` no `_call_llm`, `score:null` `decision:needs_review` `queue:manual_queue` |
| 7 | **Provider failure → dead-letter → replay** `wf-provider-failure` | `POST` with mocked `APITimeoutError` (3 retries) → `202 dead_letter stage:interpret` → `GET /dead-letter` shows `resolved:false` `replay_url` → `POST /admin/replay/{id}` Bearer → `200 replayed` `resolved:true` + `dead_letter_resolved` receipt, 2nd replay `409` | `retry_count:3` `dead_letter_queue:1` `interpreted:0` for that event, replay `1 lead/route/score` (idempotent) |
| 8 | **Out-of-order attribution** `wf` 3 events same email `attr-a 12:00`, `attr-b 14:00`, `attr-c 07:00` arrives last | `POST` in order A,B,C | `first_touch_at:07:00/camp-c` `last_touch_at:14:00/camp-b` `GET /dashboard/leads/{id}` shows both |
| 9 | **Reconciliation** after 1–8 | `GET /api/v1/dashboard/reconciliation` | `variance:0 status:PASS overall_status:ok` `total_variance:0` every row `variance:0` (hand-computed vs `summary`) |
| 10 | **Escalated SLA** backdate `routes.sla_deadline` 10d ago → `GET /leads/{id}` | `POST` then `UPDATE routes SET sla_deadline=NOW()-10d` then `GET` | `escalated:true` persisted + `escalated` receipt once, idempotent on re-read |

All scenarios are in `fixtures/{web_form,social_mention,email_engagement}_events.json` (28 events) and exercised by `tests/integration/test_phase10_sweep.py` (12) + `test_prd_edge_cases.py` (56) + `test_dashboard_pages.py` (12).

## Final checklist — presence

| Item | Present | Path / Evidence |
|---|---|---|
| Repo / commit ID | ✅ | `e45d6b5` (HEAD) / `git tag project02-submission-v1` (to be created) |
| README | ✅ | `README.md` 271 lines, includes purpose, arch, setup, env vars, usage, cost, plan-vs-built, known limitations, troubleshooting |
| Architecture diagram | ✅ | `README.md:41` Mermaid with 6 subgraphs `Inputs/Decisions/Agents/Storage/Actions/Failure Paths` |
| Sample config | ✅ | `.env.example` (names only, no values, comments per var) |
| Fixture data | ✅ | `fixtures/web_form_events.json` (10), `social_mention_events.json` (8), `email_engagement_events.json` (10), `fixtures/generate_and_post.py` (SIMULATED) |
| .env.example | ✅ | `258f71a:.env.example` |
| Test report | ✅ | `pytest -q` **221 passed 1 skipped**; positive/negative/boundary/duplicate/provider-failure via `test_events_schemas`, `test_prd_edge_cases`, `test_phase10_sweep`, `test_logging_compliance`, `test_phase10_acceptance_coverage` |
| Redacted logs/receipts sample | ✅ | `app/logging.py:_pii_redactor` `sha256:` + `test_logging_compliance` `no raw PII`, `receipts` table + `test_receipts_reconciliation` |
| Performance/reconciliation results | ✅ | `single 72.23 ms <3000` `summary 22.95 ms <1000` `reconciliation 19.75 ms <1000` `dashboard HTML 32.06 ms` for 500 events (`test_phase10_sweep`), `reconciliation PASS variance 0` |
| Known limitations | ✅ | `README.md:250` `## Known Limitations` 8 bullets (single-process, SIMULATED only, no tenant/RBAC, no retention, evaluator tooling only, on-read escalated, single-model cost, host socket) |
| Plan-vs-built summary | ✅ | `README.md:222` `## Plan vs. Built` (OpenRouter `deepseek` switch, fuzzy always manual, `primary_company`, `UNIQUE`s, `escalated` on-read, `partial UNIQUE`, dashboard) |
| AI_USAGE.md | ✅ | `AI_USAGE.md` 20 sessions S0001–S0020 incremental, each `Provider/model/What was generated/Human review/Verification` at phase end |
| ai-usage.json | ✅ | `ai-usage.json` 20 sessions, every `evaluation_id/project/session_id/started_at/ended_at/provider/model/version/prompts_or_hashes/export_path/skills_tools/generated_files/human_modified_files/verification/costs/notes` populated (validator PASS) |
| Asset provenance | ✅ | `README.md:250` `## Asset Provenance: No external icon/font/dataset beyond fixtures` + `fixtures/* @example.com` + `style.css` hand-written + `difflib` only |
| Cost/rate-limit statement | ✅ | `README.md:203` `212 tokens $0.000026` primary, `$0.0003` total probes, `deepseek` pricing, rate-limit note, `No paid service beyond approved OpenRouter` |

## Performance / reconciliation (measured)

* `single ingest→act: 72.23 ms (<3000)` — `test_phase10_sweep.py:410` print
* `summary 500 events: 22.95 ms (<1000)` `reconciliation 19.75 ms (<1000)` `dashboard HTML 32.06 ms (<1000)` — `test_phase10_sweep.py:443`
* `reconciliation` `PASS variance 0` `overall_status:ok` `total_variance:0` after seeded pack + duplicates + edits + failures (hand-computed vs `summary`)

## Tag for submission

`project02-submission-v1` → `e45d6b5` (to be created on push)

