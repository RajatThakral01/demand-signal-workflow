"""make routes.lead_id unique

Revision ID: 0009_routes_lead_id_unique
Revises: 0008_receipts
Create Date: 2026-08-20

Defect fix (Phase 8 pre-work): routes.lead_id must be UNIQUE — one route row per
lead. Before this migration `route_lead()` inserted a new Route row on every
`act()` call, so FR-2's edited-resubmission path (edit re-runs interpret→score→
act) created a SECOND routes row for the same lead_id (reachable since Phase 6).

This migration also DEDUPLICATES any legacy duplicate rows already present from
pre-fix runs: for each lead_id with more than one route, it keeps the most
recently-assigned row (highest assigned_at) and deletes the older duplicates. This
is required because the UNIQUE constraint cannot be added while duplicates exist.

Design choice: we REPLACE the existing non-unique `ix_routes_lead_id` index with a
unique constraint `uq_routes_lead_id` rather than add the constraint alongside it.
A UNIQUE constraint auto-creates a unique btree index, so keeping the plain
non-unique index would be redundant. This makes the DB — not app logic — the
guarantee that a lead has exactly one route.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_routes_lead_id_unique"
down_revision = "0008_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deduplicate legacy duplicate routes from pre-fix runs: keep the latest
    # (highest assigned_at) per lead_id, drop the older rows.
    op.execute(
        """
        DELETE FROM routes
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY lead_id
                           ORDER BY assigned_at DESC, id
                       ) AS rn
                FROM routes
            ) dup
            WHERE rn > 1
        )
        """
    )
    # Drop the redundant non-unique index; the unique constraint below creates its
    # own unique index for the same fast lookup.
    op.drop_index("ix_routes_lead_id", table_name="routes")
    op.create_unique_constraint(
        "uq_routes_lead_id", "routes", ["lead_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_routes_lead_id", "routes", type_="unique")
    op.create_index("ix_routes_lead_id", "routes", ["lead_id"])