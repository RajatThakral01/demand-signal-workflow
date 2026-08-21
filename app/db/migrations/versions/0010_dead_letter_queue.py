"""create dead_letter_queue table

Revision ID: 0010_dead_letter_queue
Revises: 0009_routes_lead_id_unique
Create Date: 2026-08-20

Phase 8b (FR-11): dead-letter queue for the interpret stage (the one external
provider call). One row per dead-lettered pipeline attempt; stage/error/retry_count
document the failure; resolved flips true when replayed/resolved (Phase 8c admin
endpoints). Rows are written atomically with a `dead_lettered` receipt.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_dead_letter_queue"
down_revision = "0009_routes_lead_id_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dead_letter_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
    )
    op.create_index("ix_dead_letter_event_id", "dead_letter_queue", ["event_id"])
    op.create_index("ix_dead_letter_resolved", "dead_letter_queue", ["resolved"])


def downgrade() -> None:
    op.drop_index("ix_dead_letter_resolved", table_name="dead_letter_queue")
    op.drop_index("ix_dead_letter_event_id", table_name="dead_letter_queue")
    op.drop_table("dead_letter_queue")