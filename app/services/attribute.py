"""Attribution service — first/last-touch tracking (FR-8, Phase 6).

One attribution_touches row per canonical identity. First-touch is set on the
first event for that identity and never replaced by a later one (immutable once
written). Last-touch updates only when the incoming event's received_at is
STRICTLY LATER than the stored last_touch_at. Ties (equal received_at) do NOT
replace last-touch — this makes the result deterministic regardless of delivery
order, because out-of-order delivery of same-timestamp events always yields the
same winner.

Edit events (event.is_edit=True) update only the denormalized source/campaign_id
on the touch(es) that reference the edited event.id. They never create a new touch
row and never update any received_at value.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AttributionTouch, Event
from app.logging import get_logger

logger = get_logger(__name__)


async def upsert_attribution(
    db: AsyncSession,
    event: Event,
    identity_id: uuid.UUID,
) -> AttributionTouch:
    """Create or update the attribution_touches row for this identity.

    DOES NOT commit — the caller (act()) owns the transaction.
    Returns the AttributionTouch ORM object.
    """
    existing = (
        await db.execute(
            select(AttributionTouch).where(AttributionTouch.identity_id == identity_id)
        )
    ).scalars().first()

    # Edit path: event.id is the same UUID as the original event (Phase 1 updates
    # the row in place), so the attribution row already references it. Only the
    # denormalized fields change; received_at values stay untouched.
    if event.is_edit and existing is not None:
        if existing.first_touch_event_id == event.id:
            existing.first_touch_source = event.source
            existing.first_touch_campaign_id = event.campaign_id
        if existing.last_touch_event_id == event.id:
            existing.last_touch_source = event.source
            existing.last_touch_campaign_id = event.campaign_id
        existing.updated_at = datetime.now(timezone.utc)
        return existing

    # Create path (no existing row yet).
    if existing is None:
        touch = AttributionTouch(
            identity_id=identity_id,
            first_touch_event_id=event.id,
            first_touch_at=event.received_at,
            first_touch_source=event.source,
            first_touch_campaign_id=event.campaign_id,
            last_touch_event_id=event.id,
            last_touch_at=event.received_at,
            last_touch_source=event.source,
            last_touch_campaign_id=event.campaign_id,
        )
        db.add(touch)
        try:
            await db.flush()  # populate touch.id; may raise if we lost a race
        except IntegrityError:
            await db.rollback()  # discards the tentative row; re-read the winner
            existing = (
                await db.execute(
                    select(AttributionTouch).where(
                        AttributionTouch.identity_id == identity_id
                    )
                )
            ).scalars().first()
            if existing is None:
                raise  # unexpected — re-raise
            # Fall through to the update path with the winner's row.
        else:
            logger.info(
                "attribution_created",
                event_id=str(event.id),
                identity_id=str(identity_id),
                first_touch_at=touch.first_touch_at.isoformat(),
            )
            return touch

    # Update path (existing row, not an edit event). Strict < and > only — ties
    # keep the existing row for both first and last touch (deterministic).
    if event.received_at < existing.first_touch_at:
        existing.first_touch_event_id = event.id
        existing.first_touch_at = event.received_at
        existing.first_touch_source = event.source
        existing.first_touch_campaign_id = event.campaign_id

    if event.received_at > existing.last_touch_at:
        existing.last_touch_event_id = event.id
        existing.last_touch_at = event.received_at
        existing.last_touch_source = event.source
        existing.last_touch_campaign_id = event.campaign_id

    existing.updated_at = datetime.now(timezone.utc)

    logger.info(
        "attribution_updated",
        event_id=str(event.id),
        identity_id=str(identity_id),
        is_edit=event.is_edit,
        first_touch_at=existing.first_touch_at.isoformat(),
        last_touch_at=existing.last_touch_at.isoformat(),
    )
    return existing