"""Dashboard router — reconciliation endpoint (FR-9, FR-10).

GET /api/v1/dashboard/reconciliation independently recomputes totals from the
`receipts` table and compares them against entity-table counts to detect any
mutations committed without a receipt (a transaction-boundary bug). No caching:
every request runs fresh SQL counts.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AttributionTouch,
    DeadLetterQueue,
    Event,
    Identity,
    Lead,
    Receipt,
    Route,
)
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


# (entity, entity_count_query, receipt_action_type) — the reconciliation pairs.
# Each row is counted by an entity-table count and an equivalent receipts count;
# variance must be 0 for the pipeline to be trusted (FR-9).
@router.get("/reconciliation", response_model=dict)
async def reconciliation(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Recompute totals from receipts and compare to entity-table counts."""
    counts = {
        "events_created": (
            await db.execute(
                select(func.count()).select_from(Event).where(
                    Event.is_valid.is_(True)
                )
            )
        ).scalar_one(),
        "events_edited": (
            await db.execute(
                select(func.count()).select_from(Event).where(Event.is_edit.is_(True))
            )
        ).scalar_one(),
        "events_rejected": (
            await db.execute(
                select(func.count()).select_from(Event).where(Event.is_valid.is_(False))
            )
        ).scalar_one(),
        "identities": (
            await db.execute(select(func.count()).select_from(Identity))
        ).scalar_one(),
        "leads": (
            await db.execute(select(func.count()).select_from(Lead))
        ).scalar_one(),
        "routes": (
            await db.execute(select(func.count()).select_from(Route))
        ).scalar_one(),
        "attribution_touches": (
            await db.execute(select(func.count()).select_from(AttributionTouch))
        ).scalar_one(),
        "dead_letter_queue": (
            await db.execute(select(func.count()).select_from(DeadLetterQueue))
        ).scalar_one(),
    }

    # receipt_count per entity-action pair (some entities also have *_updated /
    # *_rejected receipts, but the dashboard pairs above are the reconciliation
    # contract).
    receipt_pairs = {
        "events_created": ("event", "event_created"),
        "events_edited": ("event", "event_edited"),
        "events_rejected": ("event", "event_rejected"),
        "identities": ("identity", "identity_created"),
        "leads": ("lead", "lead_created"),
        "routes": ("route", "routed"),
        "attribution_touches": ("attribution_touch", "attributed_created"),
        "dead_letter_queue": ("dead_letter", "dead_lettered"),
    }

    reconciliation: list[dict] = []
    total_variance = 0
    for entity, dashboard_count in counts.items():
        entity_type, action_type = receipt_pairs[entity]
        receipt_count = (
            await db.execute(
                select(func.count()).select_from(Receipt).where(
                    Receipt.entity_type == entity_type,
                    Receipt.action_type == action_type,
                )
            )
        ).scalar_one()
        variance = abs(int(dashboard_count) - int(receipt_count))
        total_variance += variance
        reconciliation.append({
            "entity": entity,
            "dashboard_count": int(dashboard_count),
            "receipt_count": int(receipt_count),
            "variance": variance,
            "status": "ok" if variance == 0 else "mismatch",
        })

    return {
        "reconciliation": reconciliation,
        "overall_status": "ok" if total_variance == 0 else "mismatch",
        "total_variance": total_variance,
    }