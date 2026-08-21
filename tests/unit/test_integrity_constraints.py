"""Unit tests for database-enforced idempotency invariants."""

from app.db.models import Event, IdentityLink, ManualReviewQueue, Score


def test_event_partial_dedupe_index_uses_a_sql_expression():
    index = next(i for i in Event.__table__.indexes if i.name == "uq_events_dedupe_key_valid")
    where = index.dialect_options["postgresql"]["where"]
    assert hasattr(where, "_compiler_dispatch")


def test_one_identity_link_and_review_item_per_event():
    assert IdentityLink.__table__.c.event_id.unique is True
    assert ManualReviewQueue.__table__.c.event_id.unique is True


def test_one_score_per_event_for_conflict_safe_upsert():
    assert Score.__table__.c.event_id.unique is True
