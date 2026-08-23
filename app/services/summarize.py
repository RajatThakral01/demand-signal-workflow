"""Summary service — counts for the dashboard (FR-9/10, PRD §8).

Read-only aggregation for the evaluator summary screen.  All counts are fresh
SQL (no cache) and trace back to DB rows → receipts (via reconciliation).
`since`/`until` mirror the reconciliation window semantics so the two endpoints
stay comparable.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeadLetterQueue, Event, Lead, ManualReviewQueue, Score


async def get_summary(
    db: AsyncSession,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """Return counts by source / status / decision for the dashboard.

    Window (`since`/`until`) bounds the timestamp columns of each table the same
    way `dashboard.reconciliation` does, so the HTML badge and the JSON
    reconciliation stay on the same time slice.
    """

    def windowed(stmt, col):
        if since is not None:
            stmt = stmt.where(col >= since)
        if until is not None:
            stmt = stmt.where(col <= until)
        return stmt

    # Events
    total_events_stmt = select(func.count()).select_from(Event)
    total_events_stmt = windowed(total_events_stmt, Event.created_at)
    total_events = (await db.execute(total_events_stmt)).scalar_one()

    valid_events_stmt = select(func.count()).select_from(Event).where(Event.is_valid.is_(True))
    valid_events_stmt = windowed(valid_events_stmt, Event.created_at)
    valid_events = (await db.execute(valid_events_stmt)).scalar_one()

    invalid_events_stmt = select(func.count()).select_from(Event).where(Event.is_valid.is_(False))
    invalid_events_stmt = windowed(invalid_events_stmt, Event.created_at)
    invalid_events = (await db.execute(invalid_events_stmt)).scalar_one()

    # By source (valid + invalid split for transparency, but HTML sums valid)
    by_source: dict[str, int] = {}
    for source in ("web_form", "social_mention", "email_engagement"):
        stmt = select(func.count()).select_from(Event).where(Event.source == source)
        stmt = windowed(stmt, Event.created_at)
        by_source[source] = (await db.execute(stmt)).scalar_one()

    # Scores by decision
    by_decision: dict[str, int] = {}
    for decision in ("hot", "warm", "cold", "needs_review"):
        stmt = select(func.count()).select_from(Score).where(Score.decision == decision)
        stmt = windowed(stmt, Score.created_at)
        by_decision[decision] = (await db.execute(stmt)).scalar_one()

    # Leads by status
    by_status: dict[str, int] = {}
    for status in ("new", "routed", "qualified", "escalated", "closed"):
        stmt = select(func.count()).select_from(Lead).where(Lead.status == status)
        stmt = windowed(stmt, Lead.created_at)
        by_status[status] = (await db.execute(stmt)).scalar_one()

    # Pending reviews + dead letters (outstanding)
    pending_stmt = select(func.count()).select_from(ManualReviewQueue).where(ManualReviewQueue.status == "pending")
    # ManualReviewQueue has no created_at filter window in PRD; we window on id ordering? Keep unwindowed for now
    # But to stay consistent, window on the review's event.created_at via join is overkill — keep simple count
    pending_reviews = (await db.execute(pending_stmt)).scalar_one()

    dead_stmt = select(func.count()).select_from(DeadLetterQueue).where(DeadLetterQueue.resolved.is_(False))
    dead_letters = (await db.execute(dead_stmt)).scalar_one()

    total_leads_stmt = select(func.count()).select_from(Lead)
    total_leads_stmt = windowed(total_leads_stmt, Lead.created_at)
    total_leads = (await db.execute(total_leads_stmt)).scalar_one()

    return {
        "total_events": int(total_events),
        "valid_events": int(valid_events),
        "invalid_events": int(invalid_events),
        "by_source": by_source,
        "by_decision": by_decision,
        "by_status": by_status,
        "total_leads": int(total_leads),
        "pending_reviews": int(pending_reviews),
        "dead_letters": int(dead_letters),
    }
