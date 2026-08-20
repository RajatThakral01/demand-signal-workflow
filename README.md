# Demand-Signal Scoring, Routing & Attribution Workflow

Evaluation ID: DAXVORA-RAJAT-2026-08-A01
Status: in progress — see docs/Project02_Implementation_Plan_v1.md

## Setup (placeholder — full README in Phase 11)

- `cp .env.example .env`, fill in values, then `docker compose up`.
- On first boot the `db` service creates an isolated `dsw_test` database
  (`docker/init/01-test-db.sql`) alongside the dev database `dsw`.
- **Tests run against that isolated `dsw_test` database** (`TEST_DATABASE_URL`),
  so the suite's drop/create per test never touches the dev data the app operates
  on — manual walkthrough data and test data are kept apart by design.
  ```
  docker compose run --rm --no-deps \
    -v "$PWD/tests:/app/tests" -v "$PWD/pytest.ini:/app/pytest.ini" \
    --entrypoint pytest app -q
  ```

