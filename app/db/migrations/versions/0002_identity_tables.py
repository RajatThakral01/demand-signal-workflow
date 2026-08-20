"""create identities, identity_links, manual_review_queue tables

Revision ID: 0002_identity_tables
Revises: 0001_events
Create Date: 2026-08-20

Phase 2 (FR-3, Flow 3): identity resolution tables.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_identity_tables"
down_revision = "0001_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("primary_email", sa.String(), nullable=True),
        sa.Column("primary_phone", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "identity_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("match_rule", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
    )
    op.create_table(
        "manual_review_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column(
            "status", sa.String(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(
            ["candidate_identity_id"], ["identities.id"]
        ),
    )


def downgrade() -> None:
    op.drop_table("manual_review_queue")
    op.drop_table("identity_links")
    op.drop_table("identities")