"""SQLAlchemy declarative base and ORM models (PRD §6 Database Schema).

Phase 1: ``events`` table. Phase 2: ``identities``, ``identity_links`` and
``manual_review_queue`` (FR-3, Flow 3). Unique constraints are declared here so
they are enforced at the DB level (race protection) and picked up by Alembic.

Phase 2 fixup: partial unique indexes on ``identities.primary_email`` and
``primary_phone`` (WHERE <col> IS NOT NULL) so two near-simultaneous inserts of
the same email/phone cannot each create a separate canonical identity.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base. Table/column metadata is added phase by phase."""


class Event(Base):
    """Raw demand signal as ingested, before downstream resolution.

    ``dedupe_key`` carries a DB-enforced partial UNIQUE index scoped to
    ``is_valid = true`` (not just app logic) so racing duplicates cannot produce a
    second accepted row (FR-2). ``payload_hash`` is compared on a ``dedupe_key``
    hit to distinguish a true duplicate (no-op) from an edit (update + re-run
    pipeline), and is advanced to the incoming hash on every edit.

    Why the index is partial rather than a plain column UNIQUE: dedupe/edit
    detection is a contract over *accepted* events. A schema-invalid row is an
    immutable audit record (FR-1: never dropped), and two rejected submissions of
    the same bad payload are two distinct rejection facts — each needs its own row
    and its own ``event_rejected`` receipt to keep reconciliation variance at zero.
    Under a global UNIQUE(dedupe_key) the second rejection raised IntegrityError
    (a 500), and a later *corrected* resubmission was mistaken for an edit of the
    rejected row, running the pipeline on a row still flagged ``is_valid=false``.
    """

    __tablename__ = "events"

    __table_args__ = (
        Index(
            "uq_events_dedupe_key_valid",
            "dedupe_key",
            unique=True,
            postgresql_where=text("is_valid = true AND dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_event_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String, nullable=True)
    identity_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invalid_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Identity(Base):
    """A canonical contact — the ``identity_id`` resolved from demand signals.

    Multi-source and possibly-duplicated signals collapse here (FR-3). Raw PII
    needed for identity resolution (email/phone/name) lives in this DB table; it
    is redacted from structured logs (Phase 7).
    """

    __tablename__ = "identities"

    __table_args__ = (
        Index(
            "uq_identities_primary_email",
            "primary_email",
            unique=True,
            postgresql_where="primary_email IS NOT NULL",
        ),
        Index(
            "uq_identities_primary_phone",
            "primary_phone",
            unique=True,
            postgresql_where="primary_phone IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    primary_email: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_company: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdentityLink(Base):
    """Associates an event to its one canonical identity (FR-3).

    ``event_id`` is unique so an edit, retry, or racing manual-review action
    cannot attach the same event to multiple identities.
    """

    __tablename__ = "identity_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, unique=True
    )
    match_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    match_rule: Mapped[str] = mapped_column(String, nullable=False)


class ManualReviewQueue(Base):
    """Parked events whose identity match is ambiguous — human review required.

    Pipeline halts at resolution for these events until a reviewer resolves the
    entry (FR-3, Flow 3). Never auto-merged below the configured threshold.
    """

    __tablename__ = "manual_review_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, unique=True
    )
    candidate_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)


class Interpretation(Base):
    """LLM classification result for a single event (FR-4).

    One interpretation per event (``event_id`` UNIQUE). ``model_version`` and
    ``prompt_version`` are recorded on every result so the classification is
    reproducible. This is the output of the LIVE OpenRouter call — the only real
    external API call in the system. ``token_usage`` stores the OpenAI usage
    object so actual cost can be reported in the README's cost section.
    """

    __tablename__ = "interpretations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, unique=True
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    was_skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Score(Base):
    """Versioned scoring output for one event (FR-5, Phase 4).

    ``score`` is NULL when the interpretation label is ``unknown`` (policy maps it
    to null and the scorer returns ``needs_review`` with no arithmetic). ``features``
    is always present (documents the inputs even on the insufficient-data path).
    ``event_id`` is unique: an edited resubmission updates this one materialized
    score through a PostgreSQL conflict-safe upsert, so concurrent replay cannot
    manufacture duplicate score rows.
    """

    __tablename__ = "scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, unique=True
    )
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=True
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Lead(Base):
    """One lead per canonical identity (FR-6). identity_id carries a DB-level
    UNIQUE constraint as the idempotency anchor — two concurrent creates for the
    same identity_id cannot both succeed; the loser gets IntegrityError and must
    re-read and update the winner's row.

    source_event_id: the event that created/most-recently-updated this lead.
    status: new → routed (set by act); qualified/escalated/closed are future states.
    updated_at: set server-side on every update.
    """

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    source_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )  # onupdate fires when SQLAlchemy tracks attribute changes on the ORM object.
    # Always set at least one mapped column when updating a lead to ensure this triggers.


class Route(Base):
    """Routing decision for a lead (FR-7). Every route records rule_matched —
    including fallback routes — so no route is ever untraceable. escalated is
    computed-on-read for v1 (sla_deadline < now()); no scheduler is added.

    lead_id is UNIQUE: one route per lead. `route_lead()` upserts by lead_id, and
    the DB constraint is the idempotency anchor that prevents a second route row
    from FR-2's edited-resubmission path.
    """

    __tablename__ = "routes"

    __table_args__ = (
        UniqueConstraint("lead_id", name="uq_routes_lead_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False
    )
    queue: Mapped[str] = mapped_column(String, nullable=False)
    rule_matched: Mapped[str] = mapped_column(String, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AttributionTouch(Base):
    """First and last-touch attribution for a canonical identity (FR-8, Phase 6).

    One row per identity (UNIQUE on identity_id). First-touch is immutable once
    set: the logic only updates first_touch_* when the incoming event's received_at
    is STRICTLY EARLIER than the existing first_touch_at — never on equal or later.
    Last-touch updates when received_at is STRICTLY LATER than the existing
    last_touch_at (ties do NOT replace: first-received wins on equal timestamps).

    source and campaign_id are denormalized from the originating event so the
    touch row is self-contained for reporting. An edit (event.is_edit=True) updates
    the denormalized source/campaign_id on whichever touch references that event.id,
    without changing any received_at value and without creating a new touch row.
    """

    __tablename__ = "attribution_touches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=False, unique=True
    )
    # First touch — immutable once written (only replaced by strictly earlier event)
    first_touch_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    first_touch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_touch_source: Mapped[str] = mapped_column(String, nullable=False)
    first_touch_campaign_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Last touch — updates on each strictly-later event
    last_touch_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    last_touch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_touch_source: Mapped[str] = mapped_column(String, nullable=False)
    last_touch_campaign_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Housekeeping
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Receipt(Base):
    """Audit trail row written for every mutating pipeline action (FR-9, Phase 7).

    One row per action. action_type is an enum-like string (see VALID_ACTION_TYPES
    in app/services/receipts.py). entity_id + entity_type identify the primary
    object mutated. event_id and identity_id are set when the action relates to a
    known event or identity (used by the reconciliation endpoint). metadata stores
    action-specific details for audit/debugging. status is 'ok', 'error', or
    'skipped'. NOT committed inside write_receipt — the caller owns the transaction.
    """

    __tablename__ = "receipts"

    __table_args__ = (
        Index("ix_receipts_action_type", "action_type"),
        Index("ix_receipts_event_id", "event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=True
    )
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=True
    )
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ok")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeadLetterQueue(Base):
    """A pipeline stage that failed permanently after bounded retries (FR-11).

    One row per dead-lettered pipeline attempt. `stage` names the pipeline stage
    that failed (e.g. "interpret"); `error` holds the sanitized exception message
    (truncated, no raw secrets); `retry_count` records the actual attempts made;
    `resolved` flips true when a dead-letter is replayed/resolved (Phase 8c admin
    endpoint). Rows are written atomically with a `dead_lettered` receipt.

    ``event_id`` carries a partial UNIQUE index ``WHERE resolved = false`` so
    two concurrent ``simulate-failure`` calls for the same event cannot both
    create an unresolved row — the database, not just application logic, is the
    race guard (Fix 10 follow-up, mirroring ``identities`` ``uq_identities_…``
    and ``routes`` ``uq_routes_lead_id``).
    """

    __tablename__ = "dead_letter_queue"

    __table_args__ = (
        Index(
            "uq_dead_letter_queue_event_id_unresolved",
            "event_id",
            unique=True,
            postgresql_where=text("resolved = false"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
