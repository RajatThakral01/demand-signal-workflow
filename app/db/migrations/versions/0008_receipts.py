"""create receipts table

Revision ID: 0008_receipts
Revises: 0007_attribution_touches
Create Date: 2026-08-20

Phase 7 (FR-9): audit trail for every mutating pipeline action. One row per
action; event_id/identity_id are nullable FKs (some actions precede identity
resolution or involve no identity). Non-unique indexes support the reconciliation
endpoint and action-type reporting.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_receipts"
down_revision = "0007_attribution_touches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"]),
    )
    op.create_index("ix_receipts_action_type", "receipts", ["action_type"])
    op.create_index("ix_receipts_event_id", "receipts", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_receipts_event_id", table_name="receipts")
    op.drop_index("ix_receipts_action_type", table_name="receipts")
    op.drop_table("receipts")