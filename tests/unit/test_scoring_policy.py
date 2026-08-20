"""Unit tests — scoring policy application (FR-5, Phase 4).

Pure function tests on ``compute_score`` (no DB needed). Covers the structural
unknown=>null guard (no fabricated score under any inputs), tie-breaking at the
threshold boundary, fallback below all thresholds, and determinism.
"""

from decimal import Decimal

from app.services.score import _load_policy, compute_score

POLICY = _load_policy()

# pricing_inquiry base = 85, hot threshold = 70.


def _pricing(**overrides) -> dict:
    """Default inputs that yield a score of exactly 70 (hot boundary): 85 base,
    confidence ~0.82 -> round(85*0.82)=70, no bonuses."""
    base = dict(
        label="pricing_inquiry",
        confidence=Decimal("0.8"),
        source="no_bonus",   # not in source_bonus map -> 0
        consent=False,
        campaign_id=None,
        policy=POLICY,
    )
    base.update(overrides)
    # clamp confidence so the multiplier lands on 70 unless overridden.
    if "confidence" not in overrides:
        base["confidence"] = Decimal("0.82")  # round(85*0.82)=70
    return base


def test_unknown_label_returns_needs_review_and_null_score():
    score, decision, features = compute_score(
        label="unknown", confidence=Decimal("0.95"), source="web_form",
        consent=True, campaign_id="camp_123", policy=POLICY,
    )
    assert score is None
    assert decision == "needs_review"
    assert features["insufficient_data"] is True


def test_unknown_label_adversarial_inputs():
    """Even maximal bonuses + confidence cannot produce a score for unknown."""
    for label in ("unknown",):
        for confidence in (Decimal("0.5"), Decimal("0.99"), Decimal("1.0")):
            score, decision, _ = compute_score(
                label=label, confidence=confidence, source="web_form",
                consent=True, campaign_id="camp_123", policy=POLICY,
            )
            assert score is None, f"{label} produced a score under {confidence=}"
            assert decision == "needs_review"


def test_tie_breaking_at_boundary():
    """A score exactly at the hot threshold (70) maps to hot (>= comparison)."""
    score, decision, _ = compute_score(**_pricing())
    assert score == 70
    assert decision == "hot", f"expected hot at boundary 70, got {decision}"


def test_score_below_all_thresholds():
    """A score of 0 falls below every threshold -> lowest decision (cold)."""
    # Force score to 0: pricing_inquiry base scaled to ~0 via confidence=0, no
    # bonuses. round(85*0)=0.
    score, decision, _ = compute_score(**_pricing(confidence=Decimal("0"), source=None))
    assert score == 0, f"expected score 0, got {score}"
    assert decision == "cold", f"expected cold fallback, got {decision}"


def test_determinism():
    """Identical inputs produce byte-identical (score, decision, features)."""
    a = compute_score(label="integration", confidence=Decimal("0.9"),
                      source="web_form", consent=True, campaign_id="c", policy=POLICY)
    b = compute_score(label="integration", confidence=Decimal("0.9"),
                      source="web_form", consent=True, campaign_id="c", policy=POLICY)
    assert a == b
    assert str(a) == str(b)