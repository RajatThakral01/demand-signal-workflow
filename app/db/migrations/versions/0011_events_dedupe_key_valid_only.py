"""scope the events.dedupe_key uniqueness to valid rows only

Revision ID: 0011_events_dedupe_key_valid_only
Revises: 0010_dead_letter_queue
Create Date: 2026-08-21

Defect fix (Phase 8 audit, FR-1 / Error States): ``events.dedupe_key`` carried a
GLOBAL unique constraint covering schema-invalid rows. ``persist_invalid_event``
sets ``dedupe_key = hash(source|external_event_id)`` whenever both are present,
which is the common case for a payload that parses as JSON but fails schema
validation elsewhere (e.g. a bad email on an otherwise well-formed web_form).

Two consequences, both reachable:

  1. Submitting the same schema-invalid payload twice (a connector retry, or an
     evaluator probing duplicate handling on a negative case) violated the
     constraint on the second insert. ``persist_invalid_event`` has no
     IntegrityError handler, so the request returned 500 instead of the PRD's
     ``200 {event_id, is_valid: false, invalid_reason}``, breaking FR-1's
     "never silently drop an invalid event" contract in the other direction.
  2. A later *corrected* resubmission of that same external_event_id found the
     rejected row via ``find_event_by_dedupe_key`` and was classified as an EDIT
     of it. The pipeline then ran to completion (identity, score, lead, route,
     attribution) on a row still flagged ``is_valid = false`` — a live lead
     sourced from an event the API reports as rejected.

Fix: dedupe/edit detection is a contract over *accepted* events, so the
uniqueness scope becomes ``WHERE is_valid = true AND dedupe_key IS NOT NULL``.
Rejected rows keep their dedupe_key (useful for correlating a rejection with its
later correction) but no longer collide. Each rejection is now its own row with
its own ``event_rejected`` receipt, which is what keeps the
events_rejected <-> event_rejected reconciliation pair at variance 0 — collapsing
repeat rejections into one row would have desynchronized it instead.

The constraint created by Phase 1's ``sa.Column(..., unique=True)`` was named by
Postgres, not by us (``events_dedupe_key_key`` by default). We look the name up
from the catalog rather than hardcoding it, so this migration is safe against a
database whose constraint was named differently.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_events_dedupe_valid"
down_revision = "0010_dead_letter_queue"
branch_labels = None
depends_on = None


_FIND_SINGLE_COL_UNIQUE = sa.text(
    """
    SELECT c.conname
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'events'
      AND n.nspname = current_schema()
      AND c.contype = 'u'
      AND c.conkey = ARRAY[(
          SELECT a.attnum FROM pg_attribute a
          WHERE a.attrelid = t.oid AND a.attname = 'dedupe_key'
      )]::smallint[]
    LIMIT 1
    """
)


def upgrade() -> None:
    # Drop the global UNIQUE(dedupe_key) whatever Postgres named it. The name is
    # read from the catalog rather than hardcoded, and a missing constraint is a
    # no-op so this migration is safe to run against a DB built by create_all().
    existing = op.get_bind().execute(_FIND_SINGLE_COL_UNIQUE).scalar()
    if existing:
        op.drop_constraint(existing, "events", type_="unique")
    # A plain unique index (not a constraint) — Postgres only supports partial
    # uniqueness via an index, never via a table constraint.
    op.create_index(
        "uq_events_dedupe_key_valid",
        "events",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("is_valid = true AND dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_events_dedupe_key_valid", table_name="events")
    # Restoring the global constraint can fail if rejected-row duplicates exist
    # (exactly what this migration set out to allow). Null out the dedupe_key on
    # all but the oldest rejected row per key first, so the constraint can be
    # re-created without deleting any audit record.
    op.execute(
        """
        UPDATE events SET dedupe_key = NULL
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY dedupe_key
                           ORDER BY is_valid DESC, created_at, id
                       ) AS rn
                FROM events
                WHERE dedupe_key IS NOT NULL
            ) dup
            WHERE rn > 1
        )
        """
    )
    op.create_unique_constraint("events_dedupe_key_key", "events", ["dedupe_key"])
