"""Unit tests — SLA escalation boundary + receipt action-type registration.

No DB required: ``is_sla_breached`` is a pure function so the boundary is testable
without a route row or a wall clock, and ``VALID_ACTION_TYPES`` is a module-level
frozenset.

Regression scope (Phase 8 audit defect 5): ``routes.escalated`` was documented as
computed-on-read in three separate docstrings but nothing ever evaluated the
deadline, and FR-9's ``escalated`` action type was missing from
``VALID_ACTION_TYPES`` — so the transition could not have been receipted even if
some code path had set the flag.
"""

from datetime import datetime, timedelta, timezone

from app.services.escalation import is_sla_breached
from app.services.receipts import VALID_ACTION_TYPES

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


def test_deadline_in_the_future_is_not_breached():
    assert is_sla_breached(NOW + timedelta(hours=2), NOW) is False


def test_deadline_in_the_past_is_breached():
    assert is_sla_breached(NOW - timedelta(seconds=1), NOW) is True


def test_deadline_exactly_at_now_is_not_yet_breached():
    """Boundary is strict ``<``: a route sitting exactly on its deadline has not
    yet breached, matching the ``>=``-at-threshold convention used by
    resolve.should_auto_link and score._decide."""
    assert is_sla_breached(NOW, NOW) is False


def test_missing_deadline_is_never_breached():
    assert is_sla_breached(None, NOW) is False


def test_naive_deadline_is_treated_as_utc_rather_than_raising():
    """A naive timestamp from an older code path must not break a read."""
    assert is_sla_breached(datetime(2026, 8, 21, 11, 59, 59), NOW) is True
    assert is_sla_breached(datetime(2026, 8, 21, 12, 0, 1), NOW) is False


def test_escalated_is_a_registered_receipt_action_type():
    """FR-9 names `escalated` among the actions that must produce a receipt."""
    assert "escalated" in VALID_ACTION_TYPES


def test_all_fr9_named_actions_are_registered():
    """Every mutating action FR-9 enumerates must be writable as a receipt."""
    for action in (
        "event_rejected", "event_edited", "lead_created", "lead_updated",
        "routed", "escalated", "review_queued", "review_resolved",
        "dead_lettered",
    ):
        assert action in VALID_ACTION_TYPES, action
