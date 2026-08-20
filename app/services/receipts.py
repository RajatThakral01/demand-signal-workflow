"""Receipt service — audit trail for every mutating pipeline action (FR-9).

Every mutating action in the pipeline (ingest, resolve, interpret, score, act,
attribute) writes a receipt row in the same DB transaction as the action itself.
This function never commits — the caller owns the transaction.

Valid action_type values (enforced by assertion):
  event_created, event_edited, event_rejected,
  identity_created, review_queued, review_resolved,
  interpreted, scored,
  lead_created, lead_updated, routed,
  attributed_created, attributed_updated,
  dead_lettered   (Phase 8 — not wired yet)
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Receipt
from app.logging import get_logger

logger = get_logger(__name__)

VALID_ACTION_TYPES = frozenset({
    "event_created", "event_edited", "event_rejected",
    "identity_created", "review_queued", "review_resolved",
    "interpreted", "scored",
    "lead_created", "lead_updated", "routed",
    "attributed_created", "attributed_updated",
    "dead_lettered",
})


async def write_receipt(
    db: AsyncSession,
    *,
    action_type: str,
    entity_id: uuid.UUID,
    entity_type: str,
    event_id: uuid.UUID | None = None,
    identity_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    status: str = "ok",
) -> Receipt:
    """Create a receipt row. Does NOT commit — caller owns the transaction.

    Raises AssertionError if action_type is not in VALID_ACTION_TYPES — this is
    a programmer error, not a runtime failure, so we want it loud and early.
    """
    assert action_type in VALID_ACTION_TYPES, \
        f"Unknown action_type '{action_type}'. Add it to VALID_ACTION_TYPES."
    row = Receipt(
        action_type=action_type,
        entity_id=entity_id,
        entity_type=entity_type,
        event_id=event_id,
        identity_id=identity_id,
        meta=metadata or {},
        status=status,
    )
    db.add(row)
    return row