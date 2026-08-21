"""harden identity/review/score idempotency invariants

Revision ID: 0012_phase8_integrity_hardening
Revises: 0011_events_dedupe_valid
Create Date: 2026-08-21

The Phase 0-8 validation found that application-level ``SELECT`` then ``INSERT``
logic was not sufficient for three state-machine invariants under concurrent
requests. This revision makes the database the final authority:

* one identity link and one review work item per event;
* one materialized score per event; and
* a stored company field for the documented fuzzy name+company candidate rule.

Legacy duplicate links/scores are invalid states. Before adding the constraints,
the migration keeps the newest score and one deterministic identity link per
event. Receipts remain immutable audit facts; they are not deleted.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_phase8_integrity_hardening"
down_revision = "0011_events_dedupe_valid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("identities", sa.Column("primary_company", sa.String(), nullable=True))

    op.execute(
        """
        DELETE FROM identity_links duplicate
        USING identity_links kept
        WHERE duplicate.event_id = kept.event_id
          AND duplicate.id > kept.id
        """
    )
    op.execute(
        """
        DELETE FROM scores duplicate
        USING scores kept
        WHERE duplicate.event_id = kept.event_id
          AND (duplicate.created_at, duplicate.id) < (kept.created_at, kept.id)
        """
    )

    op.create_unique_constraint(
        "uq_identity_links_event_id", "identity_links", ["event_id"]
    )
    op.create_unique_constraint(
        "uq_manual_review_queue_event_id", "manual_review_queue", ["event_id"]
    )
    op.create_unique_constraint("uq_scores_event_id", "scores", ["event_id"])
    op.drop_index("ix_scores_event_id", table_name="scores")


def downgrade() -> None:
    op.create_index("ix_scores_event_id", "scores", ["event_id"])
    op.drop_constraint("uq_scores_event_id", "scores", type_="unique")
    op.drop_constraint("uq_manual_review_queue_event_id", "manual_review_queue", type_="unique")
    op.drop_constraint("uq_identity_links_event_id", "identity_links", type_="unique")
    op.drop_column("identities", "primary_company")
