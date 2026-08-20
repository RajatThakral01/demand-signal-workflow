"""Scoring service — versioned policy application (FR-5, Phase 4).

Reads the active policy file (path from ``settings.scoring_policy_version``),
applies features, and writes a ``scores`` row. The structural enforcement for
``label="unknown"`` is: the policy file maps unknown to null, and the scorer
reads that null FIRST — before any arithmetic — returning ``decision="needs_review"``
with ``score=None``. There is no code path where unknown produces a numeric score.
"""

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Event, Interpretation, Score
from app.logging import get_logger

logger = get_logger(__name__)

_POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"

# Module-level cache of the loaded policy, keyed by the policy version string so
# it reloads only if settings.scoring_policy_version changes.
_POLICY_CACHE: dict[str, dict | None] = {}


def _load_policy() -> dict:
    """Load ``app/policies/{settings.scoring_policy_version}`` (cached)."""
    version = settings.scoring_policy_version
    if version in _POLICY_CACHE and _POLICY_CACHE[version] is not None:
        return _POLICY_CACHE[version]
    path = _POLICY_DIR / version
    if not path.exists():
        raise RuntimeError(f"scoring policy file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            policy = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"scoring policy file is invalid JSON: {path}") from exc
    _POLICY_CACHE[version] = policy
    return policy


def compute_score(
    label: str,
    confidence: Decimal,
    source: str,
    consent: bool,
    campaign_id: str | None,
    policy: dict,
) -> tuple[int | None, str, dict]:
    """Return ``(score, decision, features_dict)`` for a classified event.

    Structural guard first: if the policy maps ``label`` to null (i.e. the label
    is ``unknown`` — or any null-mapped label), return ``needs_review`` with a
    null score and NO arithmetic. Nothing else runs on this path.
    """
    label_score = policy["label_scores"].get(label)
    if label_score is None:
        return None, "needs_review", {"label": label, "insufficient_data": True}

    base = int(label_score)
    confidence_multiplier_applied = bool(policy.get("confidence_multiplier_enabled", False))
    if confidence_multiplier_applied:
        base = round(base * float(confidence))

    source_bonus = int(policy.get("source_bonus", {}).get(source, 0))
    consent_bonus = int(policy.get("consent_bonus", 0)) if consent else 0
    campaign_bonus = (
        int(policy.get("campaign_bonus", 0))
        if campaign_id is not None and str(campaign_id) != "" else 0
    )

    score = base + source_bonus + consent_bonus + campaign_bonus
    clamped = max(0, min(100, score))

    decision = _decide(clamped, policy["decision_thresholds"])

    features = {
        "label": label,
        "confidence": str(confidence),
        "source": source,
        "consent": consent,
        "campaign_id": campaign_id,
        "label_base_score": base,
        "source_bonus": source_bonus,
        "consent_bonus": consent_bonus,
        "campaign_bonus": campaign_bonus,
        "confidence_multiplier_applied": confidence_multiplier_applied,
        "clamped": clamped,
    }
    return clamped, decision, features


def _decide(score: int, thresholds: list[dict]) -> str:
    """Map a numeric score to a decision using the ordered threshold list.

    Iterate thresholds in descending ``min_score`` order; the first one where
    ``score >= min_score`` wins (>= comparison — a score exactly at the boundary
    takes the higher decision, per the policy tie_break_rule). If none matches,
    fall back to the lowest threshold's decision.
    """
    for t in thresholds:
        if score >= int(t["min_score"]):
            return t["decision"]
    return thresholds[-1]["decision"]


async def score_event(
    db: AsyncSession,
    event: Event,
    identity_id: uuid.UUID | None,
    interpretation: Interpretation,
) -> Score:
    """Compute and persist a score for an event (upsert by event_id).

    Does NOT commit — the caller controls the transaction (single commit with the
    surrounding pipeline). On an edited resubmission the existing score row is
    updated in place rather than inserting a second (event_id is not unique).
    """
    policy = _load_policy()
    score_value, decision, features = compute_score(
        label=interpretation.label,
        confidence=interpretation.confidence,
        source=event.source,
        consent=event.consent,
        campaign_id=event.campaign_id,
        policy=policy,
    )

    row = (
        await db.execute(select(Score).where(Score.event_id == event.id))
    ).scalars().first()
    if row is None:
        row = Score(
            event_id=event.id,
            identity_id=identity_id,
            score=score_value,
            features=features,
            policy_version=policy.get("policy_version", settings.scoring_policy_version),
            decision=decision,
        )
        db.add(row)
    else:
        row.identity_id = identity_id
        row.score = score_value
        row.features = features
        row.policy_version = policy.get(
            "policy_version", settings.scoring_policy_version
        )
        row.decision = decision

    logger.info(
        "score_applied",
        event_id=str(event.id),
        decision=decision,
        score=score_value,
        policy_version=policy.get("policy_version", settings.scoring_policy_version),
    )
    return row