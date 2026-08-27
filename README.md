# Demand-Signal Scoring, Routing & Attribution Workflow

**Evaluation ID:** `DAXVORA-RAJAT-2026-08-A01`
**Status:** Phase 11 complete — documentation finalized, Phase 12 (final packaging) pending

A backend system that takes messy, duplicated, incomplete "demand signals" — a form fill, a social mention, an email click — and turns them into clean, trustworthy leads. It figures out who the person is, decides if the signal is worth acting on, scores it, routes it to the right queue, and proves (with numbers) that nothing was lost or double-counted along the way.

---

## The problem this solves

Founder-led businesses get interest from a lot of disconnected places: a website form, a mention in a community, someone opening a marketing email. Without a system in place:

- The same person can turn into three different contacts.
- Nobody scores leads the same way twice.
- Retries and duplicate submissions create duplicate leads.
- It's impossible to prove where a lead actually came from.

This project fixes that with one deterministic pipeline that any signal source can flow through.

---

## What it actually does

1. **Ingests** a signal from any of three sources (see "Simulated vs. real" below).
2. **Resolves identity** — matches the signal to an existing person (or creates a new one), never guessing when it's unsure.
3. **Interprets intent** — a real LLM call reads the message and labels the pain point / topic / intent.
4. **Scores** the lead using a versioned, file-based scoring policy (not hardcoded rules buried in code).
5. **Routes** the lead to a queue, with fallback handling and SLA tracking.
6. **Proves** it — every count on the dashboard reconciles back to a stored receipt. If something doesn't add up, the dashboard says so instead of hiding it.

Repeat submissions, edits, failures, and ambiguous matches are all handled explicitly rather than assumed away.

---

## Simulated vs. real — what's actually live

This is important, so it's not buried:

| Part | Status | What that means |
|---|---|---|
| The three signal sources (web form, social mention, email engagement) | **Simulated** | These are fixture files replayed against the app's own API. No real Reddit/social/email account is ever contacted. |
| The LLM classification call | **Live** | A real API call is made to OpenRouter or Groq for every event that needs interpreting. Nothing here is mocked in normal use. |
| Identity matching, scoring, routing, attribution | **Deterministic local logic** | No external service — just versioned policy files the system reads and follows. |
| The automated test suite | **Mocked** | Tests fake the LLM response so they run offline and don't cost anything. |

Full detail: [`docs/Cost_and_Limits.md`](docs/Cost_and_Limits.md).

---

## How it's built (architecture, in plain terms)

```
   Signal sources (simulated)
   web form · social mention · email
              │
              ▼
        POST /api/v1/events
              │
              ▼
   ┌─────────────────────────┐
   │  RESOLVE                │  → is this a known person?
   │  exact match / fuzzy /  │     confident match → continue
   │  manual review          │     unsure → parked for a human
   └─────────────────────────┘
              │
              ▼
   ┌─────────────────────────┐
   │  INTERPRET (live LLM)   │  → what does this person want?
   │  label + confidence     │     failures retry, then dead-letter
   └─────────────────────────┘
              │
              ▼
   ┌─────────────────────────┐
   │  SCORE                  │  → how hot is this lead?
   │  versioned policy file  │     (hot / warm / cold, explainable)
   └─────────────────────────┘
              │
              ▼
   ┌─────────────────────────┐
   │  ACT                    │  → create/update lead exactly once,
   │  route + attribute      │     route it, record where it came from
   └─────────────────────────┘
              │
              ▼
     Dashboard + reconciliation
     (every number traces to a receipt)
```

**Failure handling:** if the LLM call fails, the system retries a bounded number of times, then moves the event to a "dead letter" list instead of losing it. An admin can replay it safely later — replaying never creates a duplicate.

**Storage:** a single Postgres database. Uniqueness rules (no duplicate leads, no duplicate replies, one score per event) are enforced by the database itself, not just application code, so they hold up even under retries or concurrent requests.

---

## File structure

```
project-root/
├── app/
│   ├── main.py                # FastAPI app + router setup
│   ├── config.py              # reads .env, validates required settings
│   ├── db/
│   │   ├── models.py           # database tables (SQLAlchemy)
│   │   ├── session.py          # database connection setup
│   │   └── migrations/         # versioned schema changes (Alembic)
│   ├── schemas/                # request/response shapes (Pydantic)
│   ├── services/
│   │   ├── resolve.py          # identity matching
│   │   ├── interpret.py        # LLM classification
│   │   ├── score.py            # scoring policy
│   │   ├── act.py               # create/route/attribute
│   │   └── escalation.py       # SLA breach checks
│   ├── routers/                # API endpoints
│   ├── policies/                # versioned JSON rules (scoring, identity)
│   ├── templates/               # dashboard HTML
│   └── static/style.css
├── fixtures/                    # sample/synthetic event data + seeding script
├── tests/
│   ├── unit/
│   └── integration/
├── docs/
│   ├── PRD_Demand_Signal_Workflow_v1_2.md
│   ├── Project02_Implementation_Plan_v1.md
│   ├── Cost_and_Limits.md
│   ├── Plan_vs_Built.md
│   ├── Known_Limitations.md
│   └── evidence/                # logs/screenshots proving the claims above
├── scripts/clean_run.sh         # fresh DB + timed test run
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── requirements.txt
├── AI_USAGE.md
└── ai-usage.json
```

---

## Getting started

You need Docker and Docker Compose installed. Nothing else.

```bash
# 1. Copy the example environment file and fill in the required values
cp .env.example .env
# You must set ADMIN_API_KEY. To use the live LLM step, also set
# OPENROUTER_API_KEY or GROQ_API_KEY depending on which provider you pick.

# 2. Build and start everything (app + database)
docker compose build
docker compose up -d

# 3. Confirm it's running
curl http://localhost:8000/health
# → {"status": "ok", "db": "ok"}
```

That's it — the API, database, and dashboard are all running locally.

### Seeding sample data

```bash
python fixtures/generate_and_post.py
```

This posts a realistic mix of test events (clean signals, duplicates, edits, ambiguous matches) against your running API, so you have something to look at immediately.

### Running the tests

```bash
bash scripts/clean_run.sh
```

This resets the test database and runs the full test suite with timing, so you can see it pass from a clean state — the same thing an evaluator would run.

---

## Configuration

Everything is set through environment variables (`.env`). The important ones:

| Variable | Required? | What it's for |
|---|---|---|
| `ADMIN_API_KEY` | **Yes** | Protects admin actions (replay, simulate-failure). The app won't start without it. |
| `LLM_PROVIDER` | No (defaults to `openrouter`) | Which provider handles classification: `openrouter` or `groq`. |
| `OPENROUTER_API_KEY` | Only if using OpenRouter | Needed for any event with real text content. |
| `GROQ_API_KEY` | Only if using Groq | Same, for the Groq path. |
| `CLASSIFICATION_MODEL` | No | Which model to call. Recommended default: `openai/gpt-oss-20b` via Groq. |
| `DATABASE_URL` | No | Defaults to the Docker Compose database. |

Full list with defaults: see `.env.example` and the Configuration section of the original technical docs.

---

## Using it

```bash
# See all leads
curl http://localhost:8000/api/v1/leads | jq

# Filter by decision
curl "http://localhost:8000/api/v1/leads?decision=hot" | jq

# Check the dashboard in a browser
open http://localhost:8000/dashboard

# Confirm nothing was lost or double-counted
curl http://localhost:8000/api/v1/dashboard/reconciliation | jq
# variance should always be 0
```

If an event gets stuck in manual review (an ambiguous identity match), resolve it:

```bash
curl http://localhost:8000/api/v1/manual-review?status=pending | jq
curl -X POST http://localhost:8000/api/v1/manual-review/<review_id>/resolve \
  -H "Content-Type: application/json" -d '{"decision":"create_new"}'
```

---

## Known limitations

Being upfront about what this is and isn't:

- **Single server, single database.** No horizontal scaling or message broker — that's an intentional v1 choice, not an oversight.
- **No real connectors.** All three signal sources are test fixtures. No live webhook from a real form/social/email provider is wired up.
- **No multi-tenant support.** Built for one evaluator/one workspace at a time.
- **Nothing gets deleted.** Events and receipts are kept forever — no retention policy or GDPR-style deletion yet.
- **The evaluator dashboard is plain, not polished.** It's a tool for inspecting the system, not the branded, public-facing site (that's a separate project).

Full details, including two real bugs found during live testing and how they were fixed, are in [`docs/Known_Limitations.md`](docs/Known_Limitations.md) and [`docs/Plan_vs_Built.md`](docs/Plan_vs_Built.md).

---

## Troubleshooting

- **App won't start, complains about `ADMIN_API_KEY`:** you need to set this in `.env` — there's no default, on purpose.
- **A real event (not a short test message) fails with a 500:** you're missing the API key for whichever provider `LLM_PROVIDER` points to.
- **Fresh install and tests fail to find the test database:** run `docker compose down -v` once, then `docker compose up -d` again — the test database is only created the first time the Postgres volume is set up.
- **Running on Linux instead of macOS:** the default local test database connection assumes a macOS Postgres socket path; on Linux point it at `/var/run/postgresql` instead.

---

## More detail

- [`docs/PRD_Demand_Signal_Workflow_v1_2.md`](docs/PRD_Demand_Signal_Workflow_v1_2.md) — full requirements
- [`docs/Project02_Implementation_Plan_v1.md`](docs/Project02_Implementation_Plan_v1.md) — how it was built, phase by phase
- [`docs/Cost_and_Limits.md`](docs/Cost_and_Limits.md) — measured LLM cost, rate limits, performance
- [`docs/Plan_vs_Built.md`](docs/Plan_vs_Built.md) — what changed from the original plan, and why
- [`AI_USAGE.md`](AI_USAGE.md) — full disclosure of AI assistance used to build this project