"""enforce one unresolved dead-letter per event (Fix 10 race)

Revision ID: 0013_dlq_unresolved_unique
Revises: 0012_phase8_integrity_hardening
Create Date: 2026-08-26

Fix 10 follow-up: the simulate-failure guard was application-level only
(SELECT then INSERT). Two concurrent simulate-failure calls for the same
event could both SELECT None and then both INSERT, creating two unresolved
rows. This closes the race the same way the codebase closes every other
check-then-insert gap (identities' partial unique indexes, routes' 
uq_routes_lead_id, etc.) — with a real database constraint.

* partial unique index on dead_letter_queue(event_id) WHERE resolved = false
  — mirroring the exact style of migration 0003's uq_identities_primary_email
  (postgresql_where clause, not a plain UniqueConstraint). Only unresolved
  rows are unique per event; resolved rows remain append-only audit.

Legacy duplicate unresolved rows are invalid states (produced by the bug).
Before adding the constraint the migration deduplicates them, keeping the
oldest per event_id (deterministic) and deleting the newer ones. Receipts
are not deleted.

The SELECT check in simulate_failure is kept for the fast, friendly 409
path, but the INSERT is also wrapped in IntegrityError handling so a
genuine race is caught at the DB level and converted to the same 409.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_dlq_unresolved_unique"
down_revision = "0012_phase8_integrity_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deduplicate legacy duplicate unresolved rows, keeping oldest per event_id
    op.execute(
        """
        DELETE FROM dead_letter_queue duplicate
        USING dead_letter_queue kept
        WHERE duplicate.event_id = kept.event_id
          AND duplicate.resolved = false
          AND kept.resolved = false
          AND duplicate.id > kept.id
        """
    )
    op.create_index(
        "uq_dead_letter_queue_event_id_unresolved",
        "dead_letter_queue",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("resolved = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_dead_letter_queue_event_id_unresolved", table_name="dead_letter_queue")
