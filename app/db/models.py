"""SQLAlchemy declarative base for all ORM models.

Phase 0 ships the base only; concrete tables (events, identities, leads, ...)
land in later phases per PRD §6 Database Schema.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. Table/column metadata is added phase by phase."""