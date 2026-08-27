# Submission Checklist — DAXVORA-RAJAT-2026-08-A01

**Commit:** `HEAD` as of `2026-08-27` (post `S0036` Groq provider + `S0037` live-testing docs sweep; `f96b4f9` was last tagged `project02` baseline, now `249 passed` + docs) → will be tagged `project02-submission-v1`
**Date:** 2026-08-27

This is the "Before you send" confirmation per the assessment brief, verified start-to-finish from a genuinely clean environment (fresh clone + `cp .env.example .env` + no manual steps). Updated after the live multi-provider sweep (Groq `openai/gpt-oss-20b` now *recommended* based on real comparative testing — see `README.md: Cost & Limits` and `README.md: Plan vs. Built — Live multi-provider classification testing`).

## Clean-environment proof (updated)

* **Fresh clone:** `git clone file://$(pwd) /tmp/dsw-clean` from `HEAD` → `cp .env.example .env` → add `ADMIN_API_KEY=test_admin_key` (dummy `OPENROUTER_API_KEY`/`GROQ_API_KEY` for mocked suite; live Groq run used real `GROQ_API_KEY` from `console.groq.com` separately)
* **DB:** `DROP DATABASE IF EXISTS dsw_test; CREATE DATABASE dsw_test` (host) or `docker compose down -v` + `docker compose build` (bakes `0013_dlq_unresolved_unique` — was `0012` in earlier checklist) + `docker compose up -d --wait`
* **Full suite:** `pytest -q` **249 passed, 1 skipped in 33.64s** (host, `live` gated) — well under `<300s` (`real` via `scripts/clean_run.sh` still `~28s`). Before this docs-only sweep: `242 passed` (after `S0035` edit-500 fix), `241` (after `0013` varchar fix), `238` (after 10-fix sweep); now `+7` from `S0036` Groq provider unit tests (`test_llm_provider.py:7`).
* **Docker config:** `docker compose config` OK, `docker compose build` OK (now includes `python-multipart` dep for `pages.py:Form`), `health` `{"status":"ok","db":"ok"}` `200`
* **Seed (mocked):** `python fixtures/generate_and_post.py --dry-run` prints `28/28` `SIMULATED` events with flags `valid/duplicate/edit/manual_review/dead_letter` in `0.00s`; live seed via `requests.post` to `POST /api/v1/events` also `200`
* **Seed (live Groq, `openai/gpt-oss-20b` via `LLM_PROVIDER=groq`):** `28/28` fixture events seeded in `16.61s` via `fixtures/generate_and_post.py` → `http://localhost:8000/api/v1/events`, `ZERO` dead-letters, `GET /api/v1/dashboard/reconciliation` `variance=0` throughout, decision distribution `8 hot / 3 warm / 4 needs_review / remainder cold or duplicate/manual-review` (from `GET /api/v1/dashboard/summary`). See `docs/evidence/reconciliation_live_groq_run.json` (pasted real `variance=0` response) and `docs/evidence/logs_sample_redacted.jsonl` (redacted 7-field sample).
* **Reconciliation after seed (mocked):** `GET /api/v1/dashboard/reconciliation` `PASS variance 0`, `GET /api/v1/dashboard/summary` `by_source/web_form` etc. — same after live Groq run.

**Wall-clock:** build+up+test+seed **<60s** mocked, `16.61s` for live `28`-event Groq seed — well under **5 minutes (300s)** PRD §1.

## LIVE / SIMULATED re-verification (point of use, not just README) — updated for Groq

| Label | Where in code/logs/docs |
|---|---|
| `web_form` **SIMULATED** | `app/schemas/events.py:30` docstring, `app/routers/events.py:3` |
| `social_mention` **SIMULATED** | `app/schemas/events.py:41`, `app/routers/events.py:88` |
| `email_engagement` **SIMULATED** | `app/schemas/events.py:51` |
| Interpretation **LIVE** (selectable provider) | `app/services/interpret.py:3` "ONE real external API call — LIVE", `app/services/interpret.py:42` `GROQ_BASE_URL` / `OPENROUTER_BASE_URL`, `app/services/interpret.py:151` `_InterpretClient.get_client()` branching on `LLM_PROVIDER`, `app/config.py:36` `llm_provider`/`groq_api_key`, `README.md:25` table `openrouter` via `OPENROUTER_API_KEY` **or** `groq` via `GROQ_API_KEY` (both real, working, disclosed; Groq now proven `28/28` `16.61s` `0` dead-letters — see `README.md: Cost & Limits` and `Plan vs. Built` live-testing subsection), `app/main.py:32` description, `docs/PRD` §6 |
| `simulate-failure` **TEST-HARNESS ONLY** | `app/routers/admin.py:148` |
| Test suite **MOCKED** | `tests/integration/*` `interpret._call_llm` monkeypatched (including new `tests/unit/test_llm_provider.py:7` for provider branching), `README.md:35` |

## Secret / PII grep — actual commands and output (clean) — re-run 2026-08-27, now also covering docs/evidence

```
$ git log --all --patch | grep -E "sk-or|ghp_|AKIA" → (no output)
$ grep -R "sk-or" --include="*.py" --include="*.md" --include="*.json" . (excluding .git) → (no output)
$ grep -R "sk-or\|GROQ_API_KEY\|OPENROUTER_API_KEY" docs/evidence/ --include="*.json" --include="*.jsonl" --include="*.txt" → (no output)
$ git ls-files | grep "^\.env$" → .env.example (only)
$ git check-ignore -v .env → .gitignore:223:.env
$ grep -R "@gmail.com|@yahoo.com" . → (no output)
$ grep -R "@example.com" fixtures/ → synthetic only (ada.lovelace@example.com etc., assessment rule, no real PII)
$ grep -R "@gmail.com|@yahoo.com" docs/evidence/ → (no output)
```

*No real secret in history, no `.env` in history, `.env` git-ignored, no raw PII — only `@example.com` synthetic fixtures (allowed) and `sha256:`-hashed PII in `docs/evidence/logs_sample_redacted.jsonl` (verified `sha256:` visible, no raw email). Evidence export excludes `.env` and real keys (names only in `.env.example`, `GROQ_API_KEY=` empty).*

## Demo walkthrough — seeded scenarios and expected outcomes

| # | Scenario (from `fixtures/*` + flows) | Steps | Expected |
|---|---|---|---|
| 1 | **Flow 1 clean web_form** `wf-clear-positive-001` (pricing 50 seats) | `POST /api/v1/events` | `200 is_valid:true` `lead_id` `queue:sales_urgent` `rule:hot_any` `sla:2h` `score:hot` `attribution_touch_id` |
| 2 | **Flow 2 duplicate** same `wf-duplicate-001` twice | `POST` twice same `dedupe_key+payload_hash` | 2nd `200 duplicate:true` + no new `leads/routes/receipts` row, `events=1` |
| 3 | **Flow 2 edited** `wf-edited-001` original→corrected (20→100 seats) | `POST` original then `POST` same `dedupe_key` diff `payload_hash` | 2nd `200 is_edit:true` `payload_hash` advanced, `event_edited` receipt, 1 `Lead` (updated) `route sales_urgent→sales_priority` if decision changes, `attribution` denormalized updated |
| 4 | **Flow 3 ambiguous → manual review → create_new** `soc-ambiguous-001` (`Ada Lovlace` typo) | `POST` parks `200 status:manual_review review_id` → `POST /manual-review/{id}/resolve {"decision":"create_new"}` | `review_queued:1` then `review_resolved:1` + `identity_created:1`, `pipeline_status:resumed` `lead_id/queue/rule_matched/sla` |
| 5 | **Flow 3 merge_into** same as 4 but `{"decision":"merge_into","identity_id":"<existing>"}` | `POST` resolve | `lead` reused (`lead_op:updated`), `1 Lead` per identity, `IdentityLink` upsert |
| 6 | **Short text (<2 tokens)** `wf-short-text` `“hi”` | `POST` | `label:unknown` `model_version:none` `was_skipped:true` no `_call_llm`, `score:null` `decision:needs_review` `queue:manual_queue` |
| 7 | **Provider failure → dead-letter → replay** `wf-provider-failure` | `POST` with mocked `APITimeoutError` (3 retries) → `202 dead_letter stage:interpret` → `GET /dead-letter` shows `resolved:false` `replay_url` → `POST /admin/replay/{id}` Bearer → `200 replayed` `resolved:true` + `dead_letter_resolved` receipt, 2nd replay `409` | `retry_count:3` `dead_letter_queue:1` `interpreted:0` for that event, replay `1 lead/route/score` (idempotent) |
| 8 | **Out-of-order attribution** `wf` 3 events same email `attr-a 12:00`, `attr-b 14:00`, `attr-c 07:00` arrives last | `POST` in order A,B,C | `first_touch_at:07:00/camp-c` `last_touch_at:14:00/camp-b` `GET /dashboard/leads/{id}` shows both |
| 9 | **Reconciliation** after 1–8 | `GET /api/v1/dashboard/reconciliation` | `variance:0 status:PASS overall_status:ok` `total_variance:0` every row `variance:0` (hand-computed vs `summary`) — also `docs/evidence/reconciliation_live_groq_run.json` `variance=0` after live Groq `28/28` |
| 10 | **Escalated SLA** backdate `routes.sla_deadline` 10d ago → `GET /leads/{id}` | `POST` then `UPDATE routes SET sla_deadline=NOW()-10d` then `GET` | `escalated:true` persisted + `escalated` receipt once, idempotent on re-read |

All scenarios are in `fixtures/{web_form,social_mention,email_engagement}_events.json` (28 events) and exercised by `tests/integration/test_phase10_sweep.py` (12) + `test_prd_edge_cases.py` (56) + `test_dashboard_pages.py` (12).

## Final checklist — presence (updated after live multi-provider sweep)

| Item | Present | Path / Evidence |
|---|---|---|
| Repo / commit ID | ✅ | `HEAD` as of `2026-08-27` (post `S0036` Groq + `S0037` live-testing docs sweep) / `git tag project02-submission-v1` (to be created) |
| README | ✅ | `README.md` 294 lines, includes purpose, arch, setup, env vars (now `GROQ_API_KEY`/`LLM_PROVIDER` + `openai/gpt-oss-20b` recommended), usage, **Cost & Limits with 4 live models** (DeepSeek `212` `$0.000026` historical, Nemotron `>30s` timeout, Gemma `429` `20/min`, Groq `28/28` `16.61s` `0` dead-letters `8/3/4` + `30 RPM / 1k RPD / 8k TPM / 200k TPD` stated), plan-vs-built + **new live multi-provider subsection**, known limitations, troubleshooting |
| Architecture diagram | ✅ | `README.md:41` Mermaid with 6 subgraphs `Inputs/Decisions/Agents/Storage/Actions/Failure Paths` |
| Sample config | ✅ | `.env.example` (names only, no values, now includes `GROQ_API_KEY=` and `LLM_PROVIDER=openrouter` with `console.groq.com` comment, per existing convention) + `docker-compose.yml:35` `GROQ_API_KEY`/`LLM_PROVIDER` with `${VAR:-...}` |
| Fixture data | ✅ | `fixtures/web_form_events.json` (10), `social_mention_events.json` (8), `email_engagement_events.json` (10), `fixtures/generate_and_post.py` (SIMULATED) |
| .env.example | ✅ | `.env.example` (now `LLM_PROVIDER`/`GROQ_API_KEY` added, names only) |
| Test report | ✅ | `pytest -q` **249 passed, 1 skipped** (was `221` → `+7` Groq provider `test_llm_provider.py` + `+1` edit-500 fix + `+2` migration + `+18` earlier); `docs/evidence/pytest_final.txt` pasted real `249 passed` output as of this task (no code touched in this sweep, so unchanged) |
| Redacted logs/receipts sample | ✅ | `app/logging.py:_pii_redactor` `sha256:` + `test_logging_compliance` `no raw PII`, `receipts` table + `test_receipts_reconciliation`, **plus** `docs/evidence/logs_sample_redacted.jsonl` (4 redacted JSON-lines from live Groq run, `sha256:` visible, 7 fields `input_id/decision/reason/action/result/error/timing_ms` present) |
| Performance/reconciliation results | ✅ | `single 72.23 ms <3000` `summary 22.95 ms <1000` `reconciliation 19.75 ms <1000` `dashboard HTML 32.06 ms` for 500 events (`test_phase10_sweep`), `reconciliation PASS variance 0` (mocked), **plus** live Groq `28/28` in `16.61s` (`0.59s` avg, `<3s`) `reconciliation variance=0` pasted in `docs/evidence/reconciliation_live_groq_run.json` (8 rows `variance 0`, `dead_letter_queue 0`) |
| Known limitations | ✅ | `README.md:250` `## Known Limitations` 8 bullets (single-process, SIMULATED only, no tenant/RBAC, no retention, evaluator tooling only, on-read escalated, single-model cost, host socket) |
| Plan-vs-built summary | ✅ | `README.md:232` `## Plan vs. Built` (OpenRouter `deepseek` switch + **new** `Live multi-provider classification testing` subsection: why free-tier re-test, 4 models in order, final `openai/gpt-oss-20b` via Groq, 2 defects `S0034`/`S0035` as evidence live testing matters) + `fuzzy always manual`, `primary_company`, `UNIQUE`s, `escalated` on-read, `partial UNIQUE`, dashboard |
| AI_USAGE.md | ✅ | `AI_USAGE.md` **36 sessions** `S0001–S0037` (with `S0021` gap documented) incremental, each `Provider/model/What was generated/Human review/Verification` at phase end; last `S0037` is live multi-provider testing and evidence packaging (Muse Spark `muse-spark-1.2`, honest verification) |
| ai-usage.json | ✅ | `ai-usage.json` **36 sessions** `S0001–S0037` (with `S0021` skipped, documented in top-level `notes`), every `evaluation_id/project/session_id/started_at/ended_at/provider/model/version/prompts_or_hashes/export_path/skills_tools/generated_files/human_modified_files/verification/costs/notes` populated (validator PASS), last `S0037` mirrors `AI_USAGE.md` |
| Asset provenance | ✅ | `README.md:250` `## Asset Provenance: No external icon/font/dataset beyond fixtures` + `fixtures/* @example.com` + `style.css` hand-written + `difflib` only |
| Cost/rate-limit statement | ✅ | `README.md:205` **full rewrite** — 4 live models: DeepSeek `212` `$0.000026` (key now expired, historical), Nemotron `>30s` `timeout` at `10s`/`30s`, Gemma `429` `free-models-per-min` `limit 20`, Groq `openai/gpt-oss-20b` `28/28` `16.61s` `0` dead-letters `8/3/4` + `30 RPM / 1k RPD / 8k TPM / 200k TPD` (Groq stated, not independently verified beyond not hitting it), **recommended `openai/gpt-oss-20b` via Groq** (only one to complete full clean run), `CLASSIFICATION_MODEL` table row now `openai/gpt-oss-20b` with OpenRouter alternative still supported. No estimate presented as measurement; Groq per-call token data noted as not available after DB reset. |
| Evidence export | ✅ | `docs/evidence/` (new) — `reconciliation_live_groq_run.json` (pasted `variance=0` `PASS` 8 rows), `logs_sample_redacted.jsonl` (4 lines, `sha256:` PII, 7 fields), `pytest_final.txt` (`249 passed, 1 skipped` as of this task, no code touched) — all re-grepped for secrets (see Secret / PII grep above, now also covering `docs/evidence/`, still clean) |

## Performance / reconciliation (measured)

* `single ingest→act: 72.23 ms (<3000)` — `test_phase10_sweep.py:410` print (mocked)
* `summary 500 events: 22.95 ms (<1000)` `reconciliation 19.75 ms (<1000)` `dashboard HTML 32.06 ms (<1000)` — `test_phase10_sweep.py:443` (mocked)
* `reconciliation` `PASS variance 0` `overall_status:ok` `total_variance:0` after seeded pack + duplicates + edits + failures (hand-computed vs `summary`) — mocked
* **Live Groq:** `28/28` in `16.61s` (`0.59s` avg per event, `<3s` PRD §5) `variance=0` `dead_letter_queue 0` `8 hot / 3 warm / 4 needs_review` — pasted in `docs/evidence/reconciliation_live_groq_run.json`

## Tag for submission

`project02-submission-v1` → `HEAD` as of `2026-08-27` (to be created on push)
