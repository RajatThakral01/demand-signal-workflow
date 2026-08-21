"""Dead-letter API router — GET /api/v1/dead-letter (FR-11, PRD §6 API Design).

The PRD's Error States table promises that an event whose interpretation exhausts
its bounded retries is "visible in ``/api/v1/dead-letter``". Phase 8b wrote the
``dead_letter_queue`` rows and Phase 8c added the replay/simulate-failure admin
endpoints, but this listing endpoint was never built — so a dead-lettered event
was durable and replayable yet undiscoverable without direct SQL access. Nothing
could enumerate what needed replaying.

Read-only and unauthenticated, matching the PRD's status codes (200 only): the
bearer-token gate covers the mutating admin endpoints, not this list. Each entry
carries the fields PRD §8's dead-letter screen needs — stage, error, retry count —
plus the replay path so the screen's replay action needs no client-side URL
construction.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeadLetterQueue
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["dead-letter"])


@router.get("/dead-letter", response_model=list)
async def list_dead_letter(
    resolved: bool = Query(
        default=False,
        description="Filter by resolution state. Defaults to false (the "
                    "outstanding entries), matching the PRD's ?resolved=false.",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List dead-letter queue entries, oldest first.

    Oldest-first ordering is deliberate: the list doubles as a replay worklist,
    and the longest-stuck event is the one that should be retried first.
    """
    stmt = (
        select(DeadLetterQueue)
        .where(DeadLetterQueue.resolved.is_(resolved))
        .order_by(DeadLetterQueue.created_at, DeadLetterQueue.id)
    )
    entries = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(e.id),
            "event_id": str(e.event_id),
            "stage": e.stage,
            "error": e.error,
            "retry_count": e.retry_count,
            "resolved": e.resolved,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "replay_url": f"/api/v1/admin/replay/{e.event_id}",
        }
        for e in entries
    ]
