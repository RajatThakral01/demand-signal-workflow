"""Ingest API router — POST/GET /api/v1/events (FR-1, FR-2, Flow 1 & Flow 2).

The three signal sources are SIMULATED connectors (internal fixture generators).
No real social/email/webhook integration is ever called (PRD §2 / Appendix).
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Interpretation, Score
from app.errors import MalformedJSONError
from app.db.session import get_db_session
from app.schemas.events import event_adapter
from app.schemas.responses import EventIngestResponse
from app.services import ingest
from app.services.act import act as act_pipeline
from app.services.interpret import InterpretError, classify_event
from app.services.resolve import resolve_identity
from app.services.score import score_event

router = APIRouter(prefix="/api/v1", tags=["events"])


def _interpret_response(event_id: str, status_flag: str, identity_id: str,
                        interpret: dict | None,
                        score_row: Score | None = None,
                        act_result: dict | None = None) -> EventIngestResponse:
    """Build the ingest response from resolution + interpretation (+score+act)."""
    if interpret is None:
        return EventIngestResponse(
            event_id=event_id, is_edit=(status_flag == "edit"),
            is_valid=True, status="linked", identity_id=identity_id,
            score=score_row.score if score_row else None,
            decision=score_row.decision if score_row else None,
            score_id=str(score_row.id) if score_row else None,
            lead_id=act_result.get("lead_id") if act_result else None,
            lead_op=act_result.get("lead_op") if act_result else None,
            route_id=act_result.get("route_id") if act_result else None,
            queue=act_result.get("queue") if act_result else None,
            rule_matched=act_result.get("rule_matched") if act_result else None,
            sla_deadline=act_result.get("sla_deadline") if act_result else None,
            attribution_touch_id=act_result.get("attribution_touch_id") if act_result else None,
        )
    return EventIngestResponse(
        event_id=event_id, is_edit=(status_flag == "edit"),
        is_valid=True, status="linked", identity_id=identity_id,
        interpret_status=interpret.get("status"),
        label=interpret.get("label"),
        interpretation_id=interpret.get("interpretation_id"),
        score=score_row.score if score_row else None,
        decision=score_row.decision if score_row else None,
        score_id=str(score_row.id) if score_row else None,
        lead_id=act_result.get("lead_id") if act_result else None,
        lead_op=act_result.get("lead_op") if act_result else None,
        route_id=act_result.get("route_id") if act_result else None,
        queue=act_result.get("queue") if act_result else None,
        rule_matched=act_result.get("rule_matched") if act_result else None,
        sla_deadline=act_result.get("sla_deadline") if act_result else None,
        attribution_touch_id=act_result.get("attribution_touch_id") if act_result else None,
    )


def _dead_letter_response(event_id: str, status_flag: str, stage: str) -> EventIngestResponse:
    """Build the ingest response for a dead-lettered event (FR-11, Phase 8b).

    The pipeline halts at the failing stage: no score/lead/route. Mirrors the PRD
    Error States table: ``{"event_id":..., "status": "dead_letter", "stage": ...}``.
    """
    return EventIngestResponse(
        event_id=event_id,
        is_edit=(status_flag == "edit"),
        is_valid=True,
        status="dead_letter",
        interpret_status="error",
        stage=stage,
    )


@router.post("/events", response_model=EventIngestResponse)
async def create_event(
    request: Request,
    response: Response,
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
      * LLM provider timeout/429 after retries exhausted -> 202
        ``{"event_id":..., "status": "dead_letter", "stage": "interpret"}``
        (PRD §4 Error States).
    """
    try:
        raw = await request.body()
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise MalformedJSONError()

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

    if status_flag == "duplicate":
        return EventIngestResponse(event_id=str(event.id), duplicate=True)

    # created or edit: run identity resolution (FR-3). A fuzzy/ambiguous match
    # parks the event in the manual-review queue and the pipeline HALTS here for
    # this event until a reviewer resolves it (interpret/score/act do NOT run).
    resolution = await resolve_identity(db, event)
    if resolution["status"] == "queued_review":
        return EventIngestResponse(
            event_id=str(event.id),
            is_edit=False,
            status="manual_review",
            review_id=str(resolution["review_id"]),
        )

    identity_id = str(resolution["identity_id"])
    # Flow 1 step 4: interpretation (LIVE OpenRouter). Short text skips the LLM;
    # a provider failure surfaces as a visible interpret_status="error" rather
    # than a fabricated unknown. (Dead-letter integration is Phase 8.)
    try:
        interpret = await classify_event(db, event)
    except InterpretError:
        # Pipeline halts: no score, no lead, no route for this event. A
        # dead_letter_queue row + `dead_lettered` receipt were written inside
        # classify_event (atomic). Phase 8c replay will resume from here.
        # PRD §4 Error States requires 202 for provider timeout/429 exhaustion.
        response.status_code = 202
        return _dead_letter_response(str(event.id), status_flag, stage="interpret")

    # Flow 1 step 5: score (FR-5). Requires the interpretation ORM row. Only score
    # when interpretation succeeded (label present); a provider failure above skips
    # scoring. An edit re-run upserts the existing score row.
    interp_obj = (
        await db.execute(
            select(Interpretation).where(Interpretation.event_id == event.id)
        )
    ).scalars().first()
    score_row = None
    if interp_obj is not None:
        score_row = await score_event(db, event, resolution.get("identity_id"), interp_obj)
        await db.commit()   # Score commit (unchanged)

    # Flow 1 step 6: act — create/update lead + route (FR-6, FR-7). Uses raw UUID.
    act_result = await act_pipeline(db, event, resolution["identity_id"], score_row)

    return _interpret_response(str(event.id), status_flag, identity_id, interpret,
                               score_row, act_result)


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
    # Latest score for this event (Phase 4). event_id is not unique, so pick the
    # most recent scoring row to reflect the latest pipeline pass.
    score_row = (
        await db.execute(
            select(Score)
            .where(Score.event_id == event.id)
            .order_by(Score.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
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
        "score": score_row.score if score_row else None,
        "decision": score_row.decision if score_row else None,
        "score_features": score_row.features if score_row else None,
        "policy_version": score_row.policy_version if score_row else None,
    }