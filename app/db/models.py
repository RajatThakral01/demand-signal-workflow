"""SQLAlchemy declarative base and ORM models (PRD §6 Database Schema).

Phase 1 ships the ``events`` table (FR-1/FR-2). The unique constraint on
``dedupe_key`` is declared here so it is enforced at the DB level and picked up
by Alembic autogenerate; other tables land in later phases.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
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