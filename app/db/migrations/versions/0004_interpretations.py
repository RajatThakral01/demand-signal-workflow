"""create interpretations table

Revision ID: 0004_interpretations
Revises: 0003_identity_uniqueness
Create Date: 2026-08-20

Phase 3 (FR-4): LLM classification results. One row per event (event_id UNIQUE).
model_version / prompt_version recorded per result; token_usage stores the OpenAI
usage object for cost reporting.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_interpretations"
down_revision = "0003_identity_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interpretations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("was_skipped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("token_usage", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
    )


def downgrade() -> None:
    op.drop_table("interpretations")