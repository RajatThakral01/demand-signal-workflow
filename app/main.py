"""Demand-Signal Workflow — FastAPI application entrypoint.

Phase 0: scaffolding only. The only live endpoint is GET /health, which performs
a real `SELECT 1` against Postgres (not a hardcoded response). Feature routers
(events, leads, manual-review, dashboard, admin) mount in later phases.
"""

from fastapi import FastAPI, Response, status

from app.config import settings
from app.db.session import check_db
from app.logging import configure_logging, get_logger

configure_logging(settings.log_level)
logger = get_logger(__name__)

app = FastAPI(
    title="Demand-Signal Scoring, Routing & Attribution Workflow",
    description=(
        "Evaluation ID DAXVORA-RAJAT-2026-08-A01 | Connectors: SIMULATED "
        "(internal fixture generators). Classification: LIVE call via OpenRouter "
        "(Phase 3)."
    ),
    version="0.1.0",
)


@app.get("/health", summary="Liveness + DB connectivity probe")
async def health() -> Response:
    """Return 200 `{status: ok, db: ok}` only when the DB answers a real probe."""
    db_ok = await check_db()
    if db_ok:
        from json import dumps

        return Response(
            content=dumps({"status": "ok", "db": "ok"}),
            status_code=status.HTTP_200_OK,
            media_type="application/json",
        )

    from json import dumps

    return Response(
        content=dumps({"status": "degraded", "db": "error"}),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        media_type="application/json",
    )