# Plan vs. Built

**Deliberate deviation — OpenRouter model switch:**

* **PRD example:** `anthropic/claude-haiku-4.5` via OpenRouter.
* **Built:** `deepseek/deepseek-v4-flash` via OpenRouter (pinned `CLASSIFICATION_MODEL`).
* **Why:** materially cheaper for repeated test runs (`$0.089` vs higher Haiku pricing), same OpenAI-compatible `chat.completions` shape via `openai` SDK. `FR-4` and `interpretations.model_version` are provider/model-agnostic by design, so no PRD change — only disclosure here, in `.env.example` comment, `docker-compose.yml`, `app/config.py`, and `AI_USAGE.md` Phase 3. Cost numbers in `docs/Cost_and_Limits.md` are for the built model; swap is a one-line env change.

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
* **Four models/providers tried, in order, real outcomes (summary; full detail in `docs/Cost_and_Limits.md`):**
  1. `deepseek/deepseek-v4-flash` via OpenRouter (paid) — `212` tokens `$0.000026` historical, key now expired, no longer actively tested but stands as measured record.
  2. `nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter — functionally correct JSON, but **highly variable latency, >30s single calls, client read-timeouts at `10s` and `30s`** during real fixture seeding → would not meet PRD §5 `<3s`.
  3. `google/gemma-4-26b-a4b-it:free` via OpenRouter — hit **OpenRouter free-tier 20 req/min** (`429` body `Rate limit exceeded: free-models-per-min`, `limit 20`) under normal `28`-event burst → reproducible free-tier constraint.
  4. `openai/gpt-oss-20b` via Groq direct — **only one to complete a full clean run with zero failures at real speed**: `28/28` events in `16.61s`, `0` dead-letters, `variance=0` (see `docs/evidence/`), decisions `8 hot / 3 warm / 4 needs_review` + remainder.
* **Final decision:** **`openai/gpt-oss-20b` via Groq** (`LLM_PROVIDER=groq`, `GROQ_API_KEY`, `CLASSIFICATION_MODEL=openai/gpt-oss-20b`) — chosen because it was the **only** one of the four to finish a full `28/28` live run with **zero dead-letters** and `16.61s` avg `0.59s` (well under `3s`), while Nemotron was too slow and Gemma was rate-limited. OpenRouter (`deepseek`) remains fully supported as an alternative (`LLM_PROVIDER=openrouter`).
* **Two real defects found *during* this live testing that were *not* caught by the automated test suite — evidence that live, real-environment testing matters beyond a passing suite:**
  1. `alembic_version` `varchar(32)` overflow — `0013` revision `34` chars → `StringDataRightTruncationError` on fresh `docker compose up` (`AI_USAGE.md: S0034`, `app/db/migrations/versions/0013_dlq_unresolved_unique.py:32`). Suite never caught it because `tests/conftest.py:42` builds schema via `Base.metadata.create_all`, bypassing `alembic_version` entirely.
  2. Edit-of-dead-lettered-event `500` — second `POST` of same `external_event_id` while provider still failing hit `interpret.py:342` `IntegrityError` suppression then `MissingGreenlet` on expired `event.id` in `events.py:154` → unhandled `500` not `202` (`AI_USAGE.md: S0035`). Suite never caught it because no test did `dead-letter → edit same event while still failing`; existing dead-letter tests used distinct IDs and replay via `admin.py` doesn't touch `event.id` after `rollback`.

**Known gaps — what remains / what you'd do with more time:**

* **Live cost in README** was outstanding until Phase 3; now recorded in `docs/Cost_and_Limits.md` (real `212` tokens). Future: re-run with a larger fixture batch to publish a 50-event average cost.
* **No scheduler for `escalated`:** on-read is correct per PRD, but a background job would make `GET /dead-letter` and dashboards eventually consistent without a read triggering a write. Would add `APScheduler` or `pg_cron` + a `last_escalation_check` watermark.
* **No `tenant_id`:** single-evaluator local use (`PRD §2` future consideration). Adding it would index `tenant_id` on every table and scope dedupe/policy per tenant.
* **Fixtures are synthetic `@example.com`:** no real Reddit/ESP data (assessment rule). Swapping to real connectors would keep the same `EventIn` discriminated union and add `tenant_id` + real webhook receivers behind `SIMULATED`→`LIVE` flip.
* **No retention/TTL:** events/receipts retained indefinitely per PRD §7 v1 limitation — explicit, not oversight. Would add `retention_days` per tenant + archival job.
* **Rate-limit handling is bounded retry, not circuit breaker:** would add circuit breaker + DLQ metrics/alerts if OpenRouter 429s spike.
* **`TEST_DATABASE_URL` host default `postgresql+asyncpg://rajatthakral@/dsw_test?host=/tmp`** assumes Homebrew Postgres on `/tmp` socket (darwin). Documented, but a Linux host would need `--host /var/run/postgresql`.
