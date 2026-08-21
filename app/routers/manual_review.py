"""Manual-review API router — FR-3, Flow 3.

Ambiguous identity matches park an event in the manual-review queue; a reviewer
resolves the entry (merge_into an existing identity or create_new), which resumes
the event's pipeline. These endpoints are part of the SIMULATED/local workflow,
not a real third-party integration.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, ManualReviewQueue
from app.db.session import get_db_session
from app.logging import get_logger
from app.services.interpret import InterpretError
from app.services.pipeline import run_downstream
from app.services.resolve import get_pending_reviews, resolve_review

router = APIRouter(prefix="/api/v1/manual-review", tags=["manual-review"])
logger = get_logger(__name__)


class ReviewResolveRequest(BaseModel):
    decision: str = Field(..., pattern="^(merge_into|create_new)$")
    identity_id: str | None = None


@router.get("", response_model=list)
async def list_manual_review(
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List manual-review queue entries (optionally filtered by status).

    Default returns pending entries; pass ``?status=resolved`` for closed ones.
    """
    entries = await get_pending_reviews(db, status_filter)
    return [
        {
            "id": str(e.id),
            "event_id": str(e.event_id),
            "candidate_identity_id": str(e.candidate_identity_id)
            if e.candidate_identity_id else None,
            "reason": e.reason,
            "status": e.status,
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        }
        for e in entries
    ]


@router.post("/{review_id}/resolve")
async def resolve_manual_review(
    review_id: str,
    body: ReviewResolveRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Resolve a manual-review entry and resume the event's pipeline.

    ``decision`` is ``merge_into`` (requires ``identity_id``) or ``create_new``.
    Returns 200 with the resolved review, the chosen ``identity_id`` and the
    resumed pipeline result (label, score, decision, lead, queue, SLA) per the PRD
    API table; 404 if the review or (for merge_into) the target identity is
    missing; 409 if the review was already resolved.

    Flow 3 step 4: resolution is what un-halts the event. Ingest stopped at the
    resolve stage, so interpret -> score -> act have never run for this event and
    are executed here through the same shared runner the ingest path uses. If
    interpretation exhausts its bounded retries the event dead-letters exactly as
    it would on first pass and this returns 202 with ``status="dead_letter"`` —
    the review itself stays resolved, and the event is replayable via
    ``POST /api/v1/admin/replay/{event_id}``.
    """
    start = time.monotonic()
    entry = (
        await db.execute(select(ManualReviewQueue).where(ManualReviewQueue.id == review_id))
    ).scalars().first()
    if entry is None:
        raise HTTPException(status_code=404, detail={"error": "review_not_found"})
    if entry.status != "pending":
        raise HTTPException(status_code=409, detail={"error": "review_already_resolved"})

    try:
        result = await resolve_review(db, review_id, body.decision, body.identity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_decision", "detail": str(exc)})
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "detail": str(exc)})

    logger.info(
        "review_resolved",
        input_id=str(result["event_id"]),
        decision="resolved",
        reason=f"manual review resolved as {body.decision}",
        action="review_resolved",
        result="ok",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        review_id=str(result["review_id"]),
        identity_id=str(result["identity_id"]),
    )

    resolved = {
        "status": result["status"],
        "review_id": str(result["review_id"]),
        "identity_id": str(result["identity_id"]),
        "event_id": str(result["event_id"]),
    }

    event = (
        await db.execute(select(Event).where(Event.id == result["event_id"]))
    ).scalars().first()
    if event is None:
        # The review row carries a FK to events, so this is unreachable in practice;
        # surface it rather than silently returning a half-resolved review.
        raise HTTPException(status_code=409, detail={"error": "event_not_found"})

    try:
        outcome = await run_downstream(db, event, result["identity_id"])
    except InterpretError:
        return JSONResponse(
            status_code=202,
            content={**resolved, "pipeline_status": "dead_letter", "stage": "interpret"},
        )

    interpret = outcome["interpret"] or {}
    score_row = outcome["score_row"]
    act_result = outcome["act_result"] or {}
    return {
        **resolved,
        "pipeline_status": "resumed",
        "interpret_status": interpret.get("status"),
        "label": interpret.get("label"),
        "interpretation_id": interpret.get("interpretation_id"),
        "score": score_row.score if score_row else None,
        "decision_outcome": score_row.decision if score_row else None,
        "lead_id": act_result.get("lead_id"),
        "lead_op": act_result.get("lead_op"),
        "queue": act_result.get("queue"),
        "rule_matched": act_result.get("rule_matched"),
        "sla_deadline": act_result.get("sla_deadline"),
        "attribution_touch_id": act_result.get("attribution_touch_id"),
    }