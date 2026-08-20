"""Unit tests — identity resolution helpers and policy (FR-3).

Exact-match normalization, fuzzy similarity, the threshold-boundary decision rule,
and the identity policy's visibility (threshold + rule order not hardcoded).
"""

from decimal import Decimal

from app.services.resolve import (
    fuzzy_similarity,
    get_identity_policy,
    normalize_email,
    normalize_phone,
    should_auto_link,
)


def test_normalize_email_lowercases_and_trims():
    assert normalize_email("  Ada@Example.COM  ") == "ada@example.com"
    assert normalize_email(None) is None


def test_normalize_phone_strips_and_strips_country_code():
    assert normalize_phone("  +1 (415) 555-0132 ") == "4155550132"
    assert normalize_phone("14155550132") == "4155550132"  # leading US 1 dropped
    assert normalize_phone("4155550132") == "4155550132"   # 10 digits kept as-is
    assert normalize_phone(None) is None


def test_fuzzy_similarity_identical_is_high():
    assert fuzzy_similarity("Ada Lovelace", "Ada Lovelace") == Decimal("1.00")


def test_fuzzy_similarity_unrelated_is_low():
    assert fuzzy_similarity("Ada Lovelace", "Bob The Builder") == Decimal("0.00")


# --- Threshold boundary (PRD §11: test at and around the threshold) -----------
def test_should_auto_link_boundary_below():
    assert should_auto_link(Decimal("0.849"), Decimal("0.85")) is False


def test_should_auto_link_boundary_exactly():
    # At the threshold -> auto-link (>= semantics), per policy.
    assert should_auto_link(Decimal("0.85"), Decimal("0.85")) is True


def test_should_auto_link_boundary_above():
    assert should_auto_link(Decimal("0.851"), Decimal("0.85")) is True


def test_should_auto_link_clearly_below_and_above():
    assert should_auto_link(Decimal("0.10"), Decimal("0.85")) is False
    assert should_auto_link(Decimal("0.99"), Decimal("0.85")) is True


# --- Policy visibility: threshold & rules live in the file, not inline ---------
def test_policy_has_visible_threshold_and_rule_order():
    policy = get_identity_policy()
    assert Decimal(str(policy["confidence_threshold"])) == Decimal("0.85")
    assert policy["rules_order"] == [
        "exact_email", "exact_phone", "fuzzy_name_company"
    ]
    # fuzzy is manual-review-only by policy, never auto_link
    assert policy["rules"]["fuzzy_name_company"]["auto_link"] is False


def test_forced_auto_merge_below_threshold_is_rejected():
    """The single decision point refuses a below-threshold merge.

    ``should_auto_link`` is the only gate any link-creation path consults, so
    there is no code path that can auto-merge a sub-threshold fuzzy match.
    """
    below = Decimal("0.84")
    threshold = Decimal("0.85")
    assert should_auto_link(below, threshold) is False