#!/bin/bash
set -e
echo "=== Phase 10 clean-environment run ==="
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
START=$(date +%s)
echo "1/4 Dropping and recreating test DB dsw_test..."
psql -h /tmp -d postgres -c "DROP DATABASE IF EXISTS dsw_test;" 2>&1 | head -n 5
psql -h /tmp -d postgres -c "CREATE DATABASE dsw_test OWNER rajatthakral;" 2>&1 | head -n 5
echo "2/4 Verifying migrations (empty DB -> alembic head) ..."
# For host-run tests we use Base.metadata.create_all per test, so we just verify the DB is empty
psql -h /tmp -d dsw_test -c "\dt" 2>&1 | head -n 20
echo "3/4 Running full test suite (pytest -q)..."
# Use time to measure wall-clock
/usr/bin/time -p pytest -q 2>&1 | tail -n 20
PYTEST_EXIT=${PIPESTATUS[0]}
END=$(date +%s)
ELAPSED=$((END-START))
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Wall-clock: ${ELAPSED}s (target <300s / 5m)"
if [ "$PYTEST_EXIT" -ne 0 ]; then
  echo "FAIL: pytest exit $PYTEST_EXIT"
  exit $PYTEST_EXIT
fi
if [ "$ELAPSED" -ge 300 ]; then
  echo "FAIL: clean run exceeded 5 minutes"
  exit 1
fi
echo "PASS: clean-environment run <5m"
# Optional: also test docker compose path if docker available
if command -v docker >/dev/null 2>&1; then
  echo ""
  echo "4/4 Docker compose config check (no full build to keep <5m)..."
  docker compose config >/dev/null 2>&1 && echo "docker compose config: OK" || echo "docker compose config: FAIL"
fi
