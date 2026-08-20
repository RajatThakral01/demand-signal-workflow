"""create attribution_touches table

Revision ID: 0007_attribution_touches
Revises: 0006_leads_routes
Create Date: 2026-08-20

Phase 6 (FR-8): first/last-touch attribution. One row per identity; identity_id
carries a UNIQUE constraint (uq_attribution_touches_identity_id) as the idempotency
anchor. Postgres auto-creates a unique btree index for the constraint, so NO
redundant index is added (unlike the extra ix_leads_identity_id created in 0006).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_attribution_touches"
down_revision = "0006_leads_routes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attribution_touches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_touch_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_touch_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_touch_source", sa.String(), nullable=False),
        sa.Column("first_touch_campaign_id", sa.String(), nullable=True),
        sa.Column("last_touch_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_touch_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_touch_source", sa.String(), nullable=False),
        sa.Column("last_touch_campaign_id", sa.String(), nullable=True),
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
        sa.ForeignKeyConstraint(["first_touch_event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["last_touch_event_id"], ["events.id"]),
        sa.UniqueConstraint("identity_id", name="uq_attribution_touches_identity_id"),
    )


def downgrade() -> None:
    op.drop_table("attribution_touches")