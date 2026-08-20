"""Manual-review API router — FR-3, Flow 3.

Ambiguous identity matches park an event in the manual-review queue; a reviewer
resolves the entry (merge_into an existing identity or create_new), which resumes
the event's pipeline. These endpoints are part of the SIMULATED/local workflow,
not a real third-party integration.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ManualReviewQueue
from app.db.session import get_db_session
from app.logging import get_logger
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
    Returns 200 with the resolved review + chosen ``identity_id``; 404 if the
    review or (for merge_into) the target identity is missing; 409 if the review
    was already resolved.
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
        input_id=str(entry.event_id),
        decision="resolved",
        reason=f"manual review resolved as {body.decision}",
        action="review_resolved",
        result="ok",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        review_id=str(entry.id),
        identity_id=str(result["identity_id"]),
    )

    return {"status": result["status"], "review_id": result["review_id"],
            "identity_id": result["identity_id"]}