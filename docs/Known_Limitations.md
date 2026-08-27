# Known Limitations

Be honest — real gaps, not omitted:

* **Single-process, single-DB:** no horizontal scaling, no message broker (intentional per PRD §2). `dead_letter_queue` is in-DB, not Kafka/SQS. Throughput is limited to one Postgres instance.
* **No real connectors:** all three sources are fixture POSTs to `POST /api/v1/events`. No live Typeform/Reddit/ESP webhook is in scope (PRD §2 out-of-scope, labeled SIMULATED everywhere).
* **No multi-tenant / RBAC:** single static `ADMIN_API_KEY`, single evaluator, no per-tenant isolation. `tenant_id` would be needed for production.
* **No retention/deletion:** events and receipts are append-only, never deleted (PRD §7). GDPR-style deletion or TTL is not implemented.
* **Dashboard is evaluator tooling, not polished:** plain semantic HTML, one `style.css`, system font, no mobile/responsive polish, no asset branding (intentional per PRD §5/§8 — Project 03 is the branded surface).
* **Escalation on-read only:** `routes.escalated` flips only when a lead is read (`GET /leads`), not via a background scheduler. PRD §12 recommends this for v1, but it means a breached SLA is invisible until the next read.
* **Cost is single-model, single-run:** `$0.000026` is one canonical 212-token call; burst pricing or a different model (e.g. Haiku) would differ. No free-tier SLA is guaranteed by OpenRouter/DeepSeek.
* **Host assumption:** `TEST_DATABASE_URL` default assumes macOS Homebrew Postgres socket `/tmp`. Linux needs `/var/run/postgresql`.
