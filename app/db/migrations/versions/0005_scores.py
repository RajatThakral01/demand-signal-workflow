"""create scores table

Revision ID: 0005_scores
Revises: 0004_interpretations
Create Date: 2026-08-20

Phase 4 (FR-5): versioned scoring output. One row per scoring run; event_id is NOT
unique (an edit re-run upserts the row). score is nullable (null when label=unknown).
A non-unique index on event_id supports fast lookup on GET /api/v1/events/{id}.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_scores"
down_revision = "0004_interpretations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"]),
    )
    op.create_index("ix_scores_event_id", "scores", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_scores_event_id", table_name="scores")
    op.drop_table("scores")