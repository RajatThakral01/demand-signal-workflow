"""Demand-Signal Workflow — FastAPI application entrypoint.

Phase 0: scaffolding only. The only live endpoint is GET /health, which performs
a real `SELECT 1` against Postgres (not a hardcoded response). Feature routers
(events, leads, manual-review, dashboard, admin) mount in later phases.
"""

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.session import check_db
from app.errors import MalformedJSONError
from app.logging import configure_logging, get_logger
from app.routers.events import router as events_router
from app.routers.dashboard import router as dashboard_router
from app.routers.leads import router as leads_router
from app.routers.manual_review import router as manual_review_router

configure_logging(settings.log_level)
logger = get_logger(__name__)

app = FastAPI(
    title="Demand-Signal Scoring, Routing & Attribution Workflow",
    description=(
        "Evaluation ID DAXVORA-RAJAT-2026-08-A01 | Connectors: SIMULATED "
        "(internal fixture generators). Classification: LIVE call via OpenRouter "
        "(Phase 3)."
    ),
    version="0.2.0",
)


@app.exception_handler(MalformedJSONError)
async def _malformed_json_handler(request: Request, exc: MalformedJSONError) -> JSONResponse:
    """Centralized handler producing the PRD's flat error envelope.

    Returning a ``JSONResponse`` with a flat ``content`` dict — rather than
    raising an ``HTTPException`` (which FastAPI wraps in ``{"detail": ...}``) or
    letting the 200 ``response_model`` serialize the body — is what keeps the
    response exactly ``400 {"error": "malformed_json"}``.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "malformed_json", "detail": "request body is not valid JSON"},
    )


app.include_router(events_router)
app.include_router(leads_router)
app.include_router(manual_review_router)
app.include_router(dashboard_router)


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