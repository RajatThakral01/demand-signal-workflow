"""create partial unique indexes on identities.primary_email and primary_phone

Revision ID: 0003_identity_uniqueness
Revises: 0002_identity_tables
Create Date: 2026-08-20

Phase 2 fixup: protect against a SELECT->INSERT race in identity resolution.
Two near-simultaneous inserts of the same email/phone must not each create a
separate canonical identity. Partial indexes (WHERE <col> IS NOT NULL) enforce
uniqueness while allowing multiple NULL rows (columns are nullable).
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_identity_uniqueness"
down_revision = "0002_identity_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_identities_primary_email",
        "identities",
        ["primary_email"],
        unique=True,
        postgresql_where=sa.text("primary_email IS NOT NULL"),
    )
    op.create_index(
        "uq_identities_primary_phone",
        "identities",
        ["primary_phone"],
        unique=True,
        postgresql_where=sa.text("primary_phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_identities_primary_phone", table_name="identities")
    op.drop_index("uq_identities_primary_email", table_name="identities")