"""Admin API router — replay + simulate-failure (Phase 8c, FR-11).

Both endpoints are bearer-token gated via ADMIN_API_KEY (required by app/config.py
since Phase 0). There was no existing reusable auth dependency, so one is defined
here (`require_admin`).

  * POST /api/v1/admin/replay/{event_id} — re-runs interpret -> score -> act for a
    dead-lettered event (reusing the exact pipeline service functions), marks the
    dead_letter_queue row resolved, and receipts the resolution.
  * POST /api/v1/admin/simulate-failure — test-harness-only: dead-letters an event
    at stage="interpret" (the only stage with retry/DLQ wired) without a real call,
    so a subsequent replay can be exercised.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import DeadLetterQueue, Event, IdentityLink
from app.db.session import get_db_session
from app.services.interpret import InterpretError
from app.services.pipeline import run_downstream
from app.services.receipts import write_receipt

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_security = HTTPBearer(auto_error=False)


async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> None:
    """Reject any request without a valid ADMIN_API_KEY bearer token (401)."""
    if credentials is None or credentials.credentials != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized"},
        )


def _parse_event_id(event_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"error": "not_found"})


@router.post("/replay/{event_id}")
async def replay_event(
    event_id: str,
    db: AsyncSession = Depends(get_db_session),
    _: None = Depends(require_admin),
) -> dict:
    """Re-run the pipeline for a dead-lettered event.

    404 if the event does not exist. 409 if the event is not currently
    dead-lettered (never was, or already resolved) — the operation conflicts with
    the resource's current state, so we fail explicitly rather than silently
    succeeding with no effect. On success, the DLQ row is resolved and a
    `dead_letter_resolved` receipt written.
    """
    event_uuid = _parse_event_id(event_id)
    event = (await db.execute(select(Event).where(Event.id == event_uuid))).scalars().first()
    if event is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    dlq = (
        await db.execute(
            select(DeadLetterQueue)
            .where(DeadLetterQueue.event_id == event_uuid,
                   DeadLetterQueue.resolved.is_(False))
            .order_by(DeadLetterQueue.created_at)
        )
    ).scalars().first()
    if dlq is None:
        raise HTTPException(
            status_code=409, detail={"error": "not_dead_lettered"})

    # Identity was resolved before interpret dead-lettered the event (normal flow
    # runs resolution first), so the link already exists. Defensive: if there is
    # no link or more than one, error rather than guess (identity_links has no
    # unique constraint on event_id).
    links = (
        await db.execute(select(IdentityLink).where(IdentityLink.event_id == event_uuid))
    ).scalars().all()
    if len(links) != 1:
        raise HTTPException(
            status_code=409, detail={"error": "ambiguous_identity",
                                     "detail": f"expected 1 identity link, found {len(links)}"})
    identity_id = links[0].identity_id

    # Re-run the pipeline through the shared runner (the same one ingest and the
    # manual-review resume use), so a replayed event is interpreted, scored and
    # routed by identical code — and by identical receipts (interpreted / scored /
    # lead_created-or-updated / routed-or-route_updated / attributed_*). Every
    # stage upserts against a DB unique constraint, so a replay after a partial
    # success cannot double-write downstream.
    try:
        outcome = await run_downstream(db, event, identity_id)
    except InterpretError:
        # The replay attempt itself failed (provider still down). classify_event's
        # exhaustion path already wrote a NEW dead_letter_queue row + dead_lettered
        # receipt atomically — we surface that as a failure here.
        raise HTTPException(
            status_code=503, detail={"error": "replay_failed",
                                     "detail": "interpret still failing; event re-dead-lettered"})

    # Removed dead check that tested outcome interpretation for None — was unreachable.
    # Code trace: `classify_event` (called via `run_downstream`) always either
    # returns successfully with an `interpretations` row (skipped or LLM) or
    # raises `InterpretError` (dead-letter path). No path returns normally with
    # a None interpretation, so the check could never fire and no test ever
    # exercised it.

    act_result = outcome["act_result"] or {}

    # Mark the dead-letter resolved and receipt the mutation (FR-9). Reconciliation
    # pairs DLQ row-count vs `dead_lettered` receipts only, so flipping resolved
    # does not break that pair; the resolution is receipted for audit completeness.
    dlq.resolved = True
    await write_receipt(
        db,
        action_type="dead_letter_resolved",
        entity_id=dlq.id,
        entity_type="dead_letter",
        event_id=event.id,
        identity_id=identity_id,
        metadata={"stage": "interpret"},
    )
    await db.commit()

    return {
        "status": "replayed",
        "event_id": str(event.id),
        "lead_id": act_result.get("lead_id"),
        "queue": act_result.get("queue"),
        "rule_matched": act_result.get("rule_matched"),
        "decision": act_result.get("decision"),
    }


class SimulateFailureRequest(BaseModel):
    stage: str = Field(..., description="Pipeline stage to simulate a failure for")
    event_id: str = Field(..., description="Event to dead-letter")


@router.post("/simulate-failure")
async def simulate_failure(
    body: SimulateFailureRequest,
    db: AsyncSession = Depends(get_db_session),
    _: None = Depends(require_admin),
) -> dict:
    """Test-harness-only: dead-letter an event at a given stage without a real call.

    Only stage="interpret" is wired for retry/dead-letter, so any other stage value
    is a 400 (not a silent no-op). Produces a dead_letter_queue row + `dead_lettered`
    receipt (retry_count reflects the configured retry budget) so a subsequent replay
    can be exercised.
    """
    if body.stage != "interpret":
        raise HTTPException(
            status_code=400, detail={"error": "invalid_stage",
                                     "detail": "only stage='interpret' supports simulate-failure"})

    event_uuid = _parse_event_id(body.event_id)
    event = (await db.execute(select(Event).where(Event.id == event_uuid))).scalars().first()
    if event is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    # Guard against duplicate unresolved dead-letter rows (same pattern as
    # replay's 409 not_dead_lettered). Two simulate-failure calls on the same
    # event otherwise create two unresolved DLQ rows, breaking the 1:1 DLQ↔
    # dead_lettered receipt invariant and the dead-letter worklist.
    existing = (
        await db.execute(
            select(DeadLetterQueue).where(
                DeadLetterQueue.event_id == event_uuid,
                DeadLetterQueue.resolved.is_(False),
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail={"error": "already_dead_lettered"})

    dlq = DeadLetterQueue(
        event_id=event.id,
        stage="interpret",
        error="simulated provider failure (admin)",
        retry_count=settings.retry_max_attempts,
    )
    db.add(dlq)
    try:
        await db.flush()  # populate dlq.id before the receipt references it
        await write_receipt(
            db,
            action_type="dead_lettered",
            entity_id=dlq.id,
            entity_type="dead_letter",
            event_id=event.id,
            metadata={"stage": "interpret", "retry_count": settings.retry_max_attempts,
                      "simulated": True},
            status="error",
        )
        await db.commit()  # DLQ row + dead_lettered receipt atomic
    except IntegrityError:
        # Lost a race: another concurrent simulate-failure INSERT won the
        # partial unique index (uq_dead_letter_queue_event_id_unresolved).
        # Same pattern as _link_via_exact / create_or_update_lead: catch,
        # rollback, and convert to the same friendly 409.
        await db.rollback()
        raise HTTPException(status_code=409, detail={"error": "already_dead_lettered"})

    return {
        "status": "dead_lettered",
        "event_id": str(event.id),
        "dead_letter_id": str(dlq.id),
        "stage": "interpret",
    }