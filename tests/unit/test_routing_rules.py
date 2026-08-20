"""Unit tests — routing rules (FR-7, Phase 5).

``apply_routing_rule`` is a pure function (no DB, no side effects), so these run
in isolation against the loaded routing_rules_v1.json.
"""

from app.services.act import _load_routing_rules, apply_routing_rule

RULES = _load_routing_rules()


def test_hot_decision_routes_to_sales_urgent():
    queue, rule_matched, sla = apply_routing_rule("hot", "other", RULES)
    assert queue == "sales_urgent"
    assert rule_matched == "hot_any"
    assert sla == 2


def test_warm_pricing_inquiry_routes_to_sales_priority():
    """The more-specific warm_pricing rule must beat warm_any."""
    queue, rule_matched, sla = apply_routing_rule("warm", "pricing_inquiry", RULES)
    assert queue == "sales_priority"
    assert rule_matched == "warm_pricing"
    assert sla == 8


def test_warm_non_pricing_routes_to_sales_default():
    queue, rule_matched, sla = apply_routing_rule("warm", "feature_request", RULES)
    assert queue == "sales_default"
    assert rule_matched == "warm_any"
    assert sla == 24


def test_fallback_fires_when_no_rule_matches():
    """Critical: a decision matching no rule falls back rather than erroring."""
    queue, rule_matched, sla = apply_routing_rule("unknown_decision", "other", RULES)
    assert queue == RULES["fallback"]["queue"]
    assert rule_matched == "fallback_no_rule"
    assert sla == RULES["fallback"]["sla_hours"]


def test_rule_matched_always_present_including_fallback():
    decisions = ["hot", "warm", "needs_review", "cold", "unknown_decision"]
    for decision in decisions:
        queue, rule_matched, _ = apply_routing_rule(decision, "other", RULES)
        assert rule_matched is not None and rule_matched != "", decision
        assert queue is not None and queue != "", decision