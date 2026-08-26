"""Ingest service — hashing, dedupe and edit detection (FR-1 / FR-2).

Deterministic hashes keep dedupe reproducible: ``dedupe_key`` is a hash of
``source + external_event_id`` (stable per submission, drives the DB UNIQUE
constraint); ``payload_hash`` is a hash of the canonicalized body (compared on a
``dedupe_key`` hit to tell a true duplicate from an edit).
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event
from app.logging import get_logger
from app.services.receipts import write_receipt

logger = get_logger(__name__)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON representation (sorted keys, no whitespace).

    Two semantically identical payloads (whatever field order the connector
    emitted) hash to the same ``payload_hash`` (FR-2).
    """

    def _sort(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _sort(value[k]) for k in sorted(value)}
        if isinstance(value, list):
            return [_sort(item) for item in value]
        return value

    return json.dumps(_sort(obj), separators=(",", ":"), default=str)


def compute_payload_hash(payload: dict) -> str:
    """Hash of the canonicalized raw body (FR-2)."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_dedupe_key(source: str | None, external_event_id: str | None) -> str | None:
    """Deterministic dedupe key = hash(source + external_event_id).

    Returns ``None`` when either input is missing (a schema-invalid event may
    arrive without a stable key; such rows simply can't participate in dedupe and
    Postgres' UNIQUE constraint ignores NULLs).
    """
    if not source or not external_event_id:
        return None
    return hashlib.sha256(f"{source}|{external_event_id}".encode("utf-8")).hexdigest()


def build_identity_fields(model: Any) -> dict:
    """Project the identity-relevant fields (email/phone/name) as submitted."""
    return {
        key: value
        for key in ("email", "phone", "name", "display_name", "handle", "company")
        if (value := getattr(model, key, None)) is not None
    }


async def persist_invalid_event(
    db: AsyncSession, payload: dict, invalid_reason: str
) -> Event:
    """Persist an accepted-but-schema-invalid event (FR-1: never dropped).

    Uses the raw payload dict because no valid model exists to project fields
    from. ``dedupe_key`` may be None if the source/external id are absent; the
    UNIQUE(NOT-NULL-excluding) constraint still applies to any row that has one.

    ``invalid_reason`` is a semicolon-joined string of *all* Pydantic validation
    error messages (not just the first). Single-error payloads remain a single
    message with no separator, so existing single-error assertions stay identical.
    The full joined string is stored in ``events.invalid_reason`` and echoed in
    the ``event_rejected`` receipt metadata; ``raw_payload`` retains the original
    body for audit. This was the least-disruptive choice (no schema change, no
    new column) versus adding a new JSON list field.
    """
    start = time.monotonic()
    source = payload.get("source")
    external_event_id = payload.get("external_event_id")
    received_at = payload.get("received_at")
    if isinstance(received_at, str):
        try:
            received_at = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        except ValueError:
            received_at = None
    if received_at is None:
        # Schema-invalid rows carry no trustworthy timestamp; still needs a
        # value for the NOT NULL column — record the moment of ingestion.
        received_at = datetime.now(timezone.utc)
    event = Event(
        external_event_id=external_event_id or "",
        source=source or "unknown",
        dedupe_key=compute_dedupe_key(source, external_event_id),
        payload_hash=compute_payload_hash(payload),
        is_valid=False,
        invalid_reason=invalid_reason,
        schema_version=str(payload.get("schema_version", "1.0")),
        campaign_id=payload.get("campaign_id"),
        identity_fields=None,
        raw_payload=payload,
        consent=bool(payload.get("consent", False)),
        received_at=received_at,
    )
    db.add(event)
    await db.flush()  # populate event.id before the receipt references it
    await write_receipt(
        db,
        action_type="event_rejected",
        entity_id=event.id,
        entity_type="event",
        event_id=event.id,
        metadata={"invalid_reason": invalid_reason},
        status="error",
    )
    logger.info(
        "event_rejected",
        input_id=str(event.id),
        decision="rejected",
        reason=f"schema-invalid payload rejected: {invalid_reason}",
        action="event_rejected",
        result="error",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        invalid_reason=invalid_reason,
    )
    await db.commit()
    await db.refresh(event)
    return event


async def find_event_by_dedupe_key(db: AsyncSession, dedupe_key: str) -> Event | None:
    """Find the *accepted* event holding this dedupe_key, if any.

    Scoped to ``is_valid = True`` to match the partial unique index (migration
    0011). Rejected rows retain their dedupe_key as an audit breadcrumb but must
    never be returned here: doing so made a corrected resubmission look like an
    edit of the rejected row, running the whole pipeline on a row still flagged
    invalid instead of creating a clean accepted event.
    """
    stmt = select(Event).where(
        Event.dedupe_key == dedupe_key, Event.is_valid.is_(True)
    )
    return (await db.execute(stmt)).scalars().first()


async def create_event(db: AsyncSession, model: Any, payload: dict) -> tuple[Event, str]:
    """Persist a valid event, returning ``(event, status)`` where status is one of
    ``created`` / ``duplicate`` / ``edit`` (FR-2).

    The DB UNIQUE constraint on ``dedupe_key`` is the race-condition guard: two
    concurrent inserts of the same event both look absent at the SELECT, but only
    one INSERT commits; the loser raises ``IntegrityError`` here and is re-read as
    an exact duplicate.
    """
    start = time.monotonic()
    dedupe_key = compute_dedupe_key(model.source, model.external_event_id)
    payload_hash = compute_payload_hash(payload)

    existing = None
    if dedupe_key:
        existing = await find_event_by_dedupe_key(db, dedupe_key)

    if existing is not None:
        if existing.payload_hash == payload_hash:
            return existing, "duplicate"
        # Different payload_hash on the same dedupe_key => an edit (FR-2).
        #
        # payload_hash MUST be advanced to the incoming hash. It is the stored
        # comparand for every future submission on this dedupe_key, so leaving it
        # stale makes the row permanently "not equal" to its own content: the same
        # edited payload would re-detect as an edit on every resubmission (extra
        # `event_edited` receipts + a redundant interpret->score->act each time,
        # growing reconciliation variance without bound), while a resubmission of
        # the *original* payload would be misread as a true duplicate.
        previous_payload_hash = existing.payload_hash
        existing.is_edit = True
        existing.payload_hash = payload_hash
        existing.raw_payload = payload
        existing.identity_fields = build_identity_fields(model)
        existing.schema_version = model.schema_version
        existing.campaign_id = model.campaign_id
        existing.consent = model.consent
        existing.received_at = model.received_at
        await write_receipt(
            db,
            action_type="event_edited",
            entity_id=existing.id,
            entity_type="event",
            event_id=existing.id,
            metadata={"source": existing.source,
                      "previous_payload_hash": previous_payload_hash,
                      "payload_hash": payload_hash},
            status="ok",
        )
        logger.info(
            "event_edited",
            input_id=str(existing.id),
            decision="edit",
            reason=f"same dedupe_key, different payload_hash: edit detected (source={existing.source})",
            action="event_edited",
            result="ok",
            error=None,
            timing_ms=round((time.monotonic() - start) * 1000, 2),
        )
        await db.commit()
        await db.refresh(existing)
        return existing, "edit"

    event = Event(
        external_event_id=model.external_event_id,
        source=model.source,
        dedupe_key=dedupe_key,
        payload_hash=payload_hash,
        is_valid=True,
        schema_version=model.schema_version,
        campaign_id=model.campaign_id,
        identity_fields=build_identity_fields(model),
        raw_payload=payload,
        consent=model.consent,
        received_at=model.received_at,
    )
    db.add(event)
    try:
        await db.flush()  # populate event.id before the receipt references it
        await write_receipt(
            db,
            action_type="event_created",
            entity_id=event.id,
            entity_type="event",
            event_id=event.id,
            metadata={"source": event.source,
                      "schema_version": event.schema_version},
            status="ok",
        )
        logger.info(
            "event_created",
            input_id=str(event.id),
            decision="created",
            reason=f"new event persisted from source={event.source}",
            action="event_created",
            result="ok",
            error=None,
            timing_ms=round((time.monotonic() - start) * 1000, 2),
        )
        await db.commit()
    except IntegrityError:
        # Lost a concurrent insert race — DB constraint won. Treat as duplicate.
        await db.rollback()
        committed = await find_event_by_dedupe_key(db, dedupe_key)
        if committed is None:
            raise
        return committed, "duplicate"
    await db.refresh(event)
    return event, "created"