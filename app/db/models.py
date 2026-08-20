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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base. Table/column metadata is added phase by phase."""


class Event(Base):
    """Raw demand signal as ingested, before downstream resolution.

    ``dedupe_key`` carries a UNIQUE constraint (DB-enforced, not just app logic)
    so racing duplicates cannot produce a second row (FR-2). ``payload_hash`` is
    compared on a ``dedupe_key`` hit to distinguish a true duplicate (no-op) from
    an edit (update + re-run pipeline).
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_event_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdentityLink(Base):
    """Associates an event to the identity it resolved to, with the match rule
    and confidence that produced the link (FR-3)."""

    __tablename__ = "identity_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
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
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
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
    ``event_id`` is intentionally NOT unique: an edited resubmission re-runs the
    pipeline and upserts this row (Phase 4 upsert in score_event), so a unique
    constraint would force needless INSERT-ON-CONFLICT plumbing.
    """

    __tablename__ = "scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
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
    )


class Route(Base):
    """Routing decision for a lead (FR-7). Every route records rule_matched —
    including fallback routes — so no route is ever untraceable. escalated is
    computed-on-read for v1 (sla_deadline < now()); no scheduler is added.
    """

    __tablename__ = "routes"

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