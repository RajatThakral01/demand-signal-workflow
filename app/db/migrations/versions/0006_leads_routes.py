"""create leads and routes tables

Revision ID: 0006_leads_routes
Revises: 0005_scores
Create Date: 2026-08-20

Phase 5 (FR-6, FR-7): lead creation + routing + SLA. leads.identity_id carries a
DB-level UNIQUE constraint (uq_leads_identity_id) as the idempotency anchor —
two concurrent creates for the same identity cannot both succeed. routes records
rule_matched (NOT NULL) on every route including the fallback; ix_routes_lead_id
supports fast lookup. ix_leads_status supports filtering by status.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_leads_routes"
down_revision = "0005_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"]),
        sa.ForeignKeyConstraint(["source_event_id"], ["events.id"]),
        sa.UniqueConstraint("identity_id", name="uq_leads_identity_id"),
    )
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_identity_id", "leads", ["identity_id"], unique=True)

    op.create_table(
        "routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queue", sa.String(), nullable=False),
        sa.Column("rule_matched", sa.String(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
    )
    op.create_index("ix_routes_lead_id", "routes", ["lead_id"])


def downgrade() -> None:
    op.drop_index("ix_routes_lead_id", table_name="routes")
    op.drop_table("routes")
    op.drop_index("ix_leads_identity_id", table_name="leads")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_table("leads")