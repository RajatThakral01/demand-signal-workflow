"""Ingest API router — POST/GET /api/v1/events (FR-1, FR-2, Flow 1 & Flow 2).

The three signal sources are SIMULATED connectors (internal fixture generators).
No real social/email/webhook integration is ever called (PRD §2 / Appendix).
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event
from app.db.session import get_db_session
from app.schemas.events import event_adapter
from app.schemas.responses import EventIngestResponse
from app.services import ingest

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events", response_model=EventIngestResponse)
async def create_event(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> EventIngestResponse:
    """Ingest a single demand signal from any of the three SIMULATED sources.

    Error States (PRD §Error States / FR-1):
      * Malformed JSON  -> 400 ``{"error": "malformed_json"}``, NOT persisted.
      * Valid JSON, fails schema -> 200 ``is_valid=false`` + ``invalid_reason``,
        persisted (an isolated, never-dropped row).
      * True duplicate (same dedupe_key + payload_hash) -> 200 ``duplicate=true``.
      * Edit (same dedupe_key, different payload_hash) -> 200, row updated,
        ``is_edit=true``.
    """
    try:
        raw = await request.body()
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "malformed_json", "detail": "request body is not valid JSON"},
        )

    if not isinstance(payload, dict):
        event = await ingest.persist_invalid_event(
            db, {}, "expected_json_object"
        )
        return EventIngestResponse(
            event_id=str(event.id),
            is_valid=False,
            invalid_reason="expected_json_object",
        )

    try:
        model = event_adapter.validate_python(payload)
    except ValidationError as exc:
        reason = exc.errors()[0]["msg"]
        event = await ingest.persist_invalid_event(db, payload, reason)
        return EventIngestResponse(
            event_id=str(event.id),
            is_valid=False,
            invalid_reason=reason,
        )

    event, status_flag = await ingest.create_event(db, model, payload)

    if status_flag == "created":
        return EventIngestResponse(event_id=str(event.id))
    if status_flag == "duplicate":
        return EventIngestResponse(event_id=str(event.id), duplicate=True)
    # edit
    return EventIngestResponse(event_id=str(event.id), is_edit=True)


@router.get("/events/{event_id}", response_model=dict)
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return the full persisted event plus its pipeline state."""
    stmt = select(Event).where(Event.id == event_id)
    event = (await db.execute(stmt)).scalars().first()
    if event is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {
        "event_id": str(event.id),
        "external_event_id": event.external_event_id,
        "source": event.source,
        "dedupe_key": event.dedupe_key,
        "payload_hash": event.payload_hash,
        "is_edit": event.is_edit,
        "schema_version": event.schema_version,
        "campaign_id": event.campaign_id,
        "identity_fields": event.identity_fields,
        "consent": event.consent,
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "is_valid": event.is_valid,
        "invalid_reason": event.invalid_reason,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }