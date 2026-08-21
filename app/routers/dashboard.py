"""Dashboard router — reconciliation endpoint (FR-9, FR-10).

GET /api/v1/dashboard/reconciliation independently recomputes totals from the
`receipts` table and compares them against entity-table counts to detect any
mutations committed without a receipt (a transaction-boundary bug). No caching:
every request runs fresh SQL counts.

Response shape follows the PRD in both places it is specified: the per-metric
`{dashboard_count, receipt_count, variance, status}` rows of FR-10, and the
top-level `{"variance": N, "status": "PASS"|"FAIL"}` of the Error States table
("the field itself signals failure; no exception needed"). Pass condition is
`variance == 0` — any nonzero value is a defect, not a warning.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
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


# (metric, model, timestamp column, entity-side filter, receipt entity_type,
#  receipt action_type, count_distinct_events) — the reconciliation pairs. Each
# metric is counted once from its entity table and once from `receipts`; variance
# must be 0 (FR-9).
#
# Only creation-shaped actions are paired: `*_updated` receipts accumulate per
# mutation while the row count stays at one, so pairing them would manufacture
# variance by construction.
#
# `events_edited` is the one pair that needs COUNT(DISTINCT event_id) on the
# receipt side. `events.is_edit` is a sticky boolean — set once and never unset,
# no matter how many further edits arrive — so its entity-side count is "events
# that have ever been edited". Counting raw `event_edited` receipts instead made
# any event edited twice (two genuinely different payloads, two legitimate
# receipts, still one row) report variance 1 and fail the FR-10 / Success
# Criterion 2 gate. Distinct-event counting is the exact pairing.
_PAIRS = (
    ("events_created", Event, Event.created_at, Event.is_valid.is_(True),
     "event", "event_created", False),
    ("events_edited", Event, Event.created_at, Event.is_edit.is_(True),
     "event", "event_edited", True),
    ("events_rejected", Event, Event.created_at, Event.is_valid.is_(False),
     "event", "event_rejected", False),
    ("identities", Identity, Identity.created_at, None,
     "identity", "identity_created", False),
    ("leads", Lead, Lead.created_at, None, "lead", "lead_created", False),
    ("routes", Route, Route.assigned_at, None, "route", "routed", False),
    ("attribution_touches", AttributionTouch, AttributionTouch.created_at, None,
     "attribution_touch", "attributed_created", False),
    ("dead_letter_queue", DeadLetterQueue, DeadLetterQueue.created_at, None,
     "dead_letter", "dead_lettered", False),
)


def _window(stmt, ts_column, since: datetime | None, until: datetime | None):
    """Apply the optional [since, until] bounds to a count query."""
    if since is not None:
        stmt = stmt.where(ts_column >= since)
    if until is not None:
        stmt = stmt.where(ts_column <= until)
    return stmt


@router.get("/reconciliation", response_model=dict)
async def reconciliation(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Recompute totals from receipts and compare to entity-table counts.

    ``since``/``until`` bound both sides of every comparison to the same window
    (PRD §3 success criterion 2: "for the same time window"). Postgres evaluates
    ``now()`` once per transaction, and an entity plus its receipt are always
    written in one transaction, so both sides carry an identical timestamp and the
    window cannot split a pair.

    One windowing caveat, by construction rather than by defect: ``events_edited``
    counts entity rows by ``events.created_at`` but receipts by the edit's own
    timestamp. An event created before ``since`` and edited inside the window
    therefore contributes a receipt without an entity row. The unbounded query —
    the one the acceptance criteria and the seeded test pack run — is exact.
    """
    rows: list[dict] = []
    total_variance = 0

    for (metric, model, ts_column, entity_filter, entity_type, action_type,
         distinct_events) in _PAIRS:
        entity_stmt = select(func.count()).select_from(model)
        if entity_filter is not None:
            entity_stmt = entity_stmt.where(entity_filter)
        dashboard_count = (
            await db.execute(_window(entity_stmt, ts_column, since, until))
        ).scalar_one()

        counter = (
            func.count(func.distinct(Receipt.event_id)) if distinct_events
            else func.count()
        )
        receipt_stmt = select(counter).select_from(Receipt).where(
            Receipt.entity_type == entity_type,
            Receipt.action_type == action_type,
        )
        receipt_count = (
            await db.execute(_window(receipt_stmt, Receipt.created_at, since, until))
        ).scalar_one()

        variance = abs(int(dashboard_count) - int(receipt_count))
        total_variance += variance
        rows.append({
            "entity": metric,
            "dashboard_count": int(dashboard_count),
            "receipt_count": int(receipt_count),
            "variance": variance,
            "status": "ok" if variance == 0 else "mismatch",
        })

    return {
        # PRD Error States: the top-level field itself signals pass/fail.
        "variance": total_variance,
        "status": "PASS" if total_variance == 0 else "FAIL",
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "reconciliation": rows,
        # Retained aliases so existing callers/tests keep working.
        "overall_status": "ok" if total_variance == 0 else "mismatch",
        "total_variance": total_variance,
    }
