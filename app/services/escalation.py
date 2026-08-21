"""SLA escalation (FR-7, FR-9; PRD §12 open item).

The PRD resolves the scheduler-vs-on-read question explicitly: escalation is
**computed on read** (``sla_deadline < now()``) for v1, so no background scheduler
dependency is introduced. That decision is recorded here and flagged in the
plan-vs-built writeup.

Before this module the decision was documented in three docstrings but never
implemented: ``routes.escalated`` defaulted to false, nothing ever evaluated the
deadline, and no code path could write it — so ``GET /api/v1/leads/{id}`` returned
``"escalated": false`` for a route whose SLA had lapsed days earlier. FR-9 also
names ``escalated`` as a mutating action requiring a receipt, but the action type
was absent from ``VALID_ACTION_TYPES``, so even a manual flip could not have been
receipted.

Design: on-read *detection*, persisted once. The read path evaluates the deadline
and, the first time it observes a breach, flips ``routes.escalated`` and writes an
``escalated`` receipt. Subsequent reads see the flag already set and write
nothing. Two properties make a side-effecting GET acceptable here:

  * It is idempotent and monotonic — the transition is only ever false -> true,
    guarded on the current value, so concurrent reads converge on one flip and
    at most one receipt.
  * It keeps receipts as the source of truth. A purely-derived boolean would show
    an escalation on the dashboard that no receipt could account for, which is
    precisely what the FR-9/FR-10 reconciliation contract exists to prevent.

Escalation is recorded on the route, not the lead: ``routes.escalated`` is the
column the PRD schema dedicates to it and ``sla_deadline`` lives there too.
``leads.status`` keeps its routing-lifecycle value.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Route
from app.logging import get_logger
from app.services.receipts import write_receipt

logger = get_logger(__name__)


def is_sla_breached(sla_deadline: datetime | None, now: datetime) -> bool:
    """True when ``now`` is strictly past ``sla_deadline``.

    Boundary semantics: a route sitting exactly ON its deadline is NOT yet
    breached (strict ``<``), mirroring the ``>=``-at-threshold convention used by
    ``resolve.should_auto_link`` and ``score._decide``. Exposed as a pure function
    so the boundary is directly testable without a route row or a clock.

    A naive ``sla_deadline`` is treated as UTC rather than raising, so a row
    written by an older code path cannot break a read.
    """
    if sla_deadline is None:
        return False
    if sla_deadline.tzinfo is None:
        sla_deadline = sla_deadline.replace(tzinfo=timezone.utc)
    return sla_deadline < now


async def evaluate_escalation(
    db: AsyncSession,
    route: Route | None,
    identity_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> bool:
    """Return the route's escalation state, persisting a newly-observed breach.

    Returns False for a missing route. Does NOT commit — the caller owns the
    transaction, consistent with ``write_receipt`` and the rest of the services.
    """
    if route is None:
        return False
    if route.escalated:
        return True  # already recorded and receipted; nothing to do

    now = now or datetime.now(timezone.utc)
    if not is_sla_breached(route.sla_deadline, now):
        return False

    route.escalated = True
    await write_receipt(
        db,
        action_type="escalated",
        entity_id=route.id,
        entity_type="route",
        identity_id=identity_id,
        metadata={
            "queue": route.queue,
            "rule_matched": route.rule_matched,
            "sla_deadline": route.sla_deadline.isoformat() if route.sla_deadline else None,
            "detected_at": now.isoformat(),
        },
    )
    logger.info(
        "route_escalated",
        input_id=str(route.lead_id),
        decision="escalated",
        reason=f"sla_deadline passed for queue={route.queue}",
        action="escalated",
        result="ok",
        error=None,
        timing_ms=0.0,
        route_id=str(route.id),
        queue=route.queue,
    )
    return True
