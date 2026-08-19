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
- **Human review / changes:** pending Rajat review this commit.
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

*Model/provider note: model identifier is `~deepseek/deepseek-v4-flash-latest`
served via OpenRouter. Exact pinned model string recorded in each session entry.*