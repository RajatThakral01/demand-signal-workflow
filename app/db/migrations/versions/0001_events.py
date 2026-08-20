"""create events table

Revision ID: 0001_events
Revises:
Create Date: 2026-08-20

Phase 1 (FR-1 / FR-2): the ``events`` table with a DB-enforced UNIQUE constraint
on ``dedupe_key`` (protects against racing duplicates at the Postgres level, not
just app logic).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_events"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_event_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=True, unique=True),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("is_edit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("campaign_id", sa.String(), nullable=True),
        sa.Column("identity_fields", postgresql.JSONB(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("consent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("invalid_reason", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("events")