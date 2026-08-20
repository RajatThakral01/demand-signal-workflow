#!/usr/bin/env sh
# In-container entrypoint: apply any pending Alembic migrations, then start uvicorn.
# Running `alembic upgrade head` on every boot is what makes a fresh `docker
# compose up` self-sufficient — containers never rely on migrations being run
# manually by a human (Phase 1 review item).
set -e

echo "[entrypoint] applying database migrations (alembic upgrade head)"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000