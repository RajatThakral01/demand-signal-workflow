"""Integration tests — score pipeline (FR-5, Phase 4).

POSTs events through the real API with a mocked LLM (monkeypatched
``interpret._call_llm``) so scoring runs deterministically, then verifies the
response fields and the underlying ``scores`` row.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Score
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": "wf-score-0001",
        "received_at": NOW.isoformat(),
        "consent": True,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "company": "Analytical Engines",
        "message": (
            "Our team is evaluating your pricing tiers and we would like a "
            "quote for annual commitment discounts across our org"
        ),
    }
    payload.update(overrides)
    return payload


def _fake_call_llm(label="pricing_inquiry", confidence=0.9):
    """Return a deterministic async stand-in for interpret._call_llm."""

    async def _fake(*args, **kwargs):
        return {
            "label": label,
            "confidence": confidence,
            "reason": "deterministic test classification",
            "_model": "deepseek/deepseek-v4-flash",
            "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return _fake


async def test_score_recorded_on_happy_path(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    resp = await client.post("/api/v1/events", json=_web_form())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "linked"
    assert isinstance(body["score"], int) and 0 <= body["score"] <= 100
    assert body["decision"] in ("hot", "warm", "cold", "needs_review")
    assert body["score_id"]

    rows = (await db_session.execute(select(Score))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.score == body["score"]
    assert row.decision == body["decision"]
    for key in ("label", "confidence", "source", "consent", "campaign_id",
                "label_base_score", "source_bonus", "consent_bonus",
                "campaign_bonus", "confidence_multiplier_applied", "clamped"):
        assert key in row.features, key
    assert row.policy_version


async def test_unknown_label_event_produces_needs_review(client, db_session):
    # Short message (<8 tokens) -> LLM skipped -> label "unknown" -> needs_review.
    resp = await client.post("/api/v1/events", json=_web_form(message="Hi"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "unknown"
    assert body["decision"] == "needs_review"
    assert body["score"] is None

    rows = (await db_session.execute(select(Score))).scalars().all()
    assert len(rows) == 1
    assert rows[0].score is None
    assert rows[0].decision == "needs_review"


async def test_score_determinism_through_pipeline(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    a = await client.post("/api/v1/events", json=_web_form(external_event_id="det-1"))
    b = await client.post("/api/v1/events", json=_web_form(external_event_id="det-2"))
    sa_ = a.json()
    sb_ = b.json()
    assert sa_["score"] == sb_["score"]
    assert sa_["decision"] == sb_["decision"]

    rows = (await db_session.execute(select(Score))).scalars().all()
    assert len(rows) == 2
    r1, r2 = rows[0], rows[1]
    assert r1.score == r2.score
    assert r1.decision == r2.decision
    assert r1.policy_version == r2.policy_version
    assert r1.features == r2.features