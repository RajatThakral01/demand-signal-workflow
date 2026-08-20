"""Integration tests — act pipeline (FR-6, FR-7, Phase 5).

POSTs events through the real API (mocked LLM) and asserts lead/route behavior:
exactly-once lead creation, fallback routing on unmatched decisions, the
single-commit atomicity contract, and the leads read endpoints.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import Lead, Route
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": f"act-{uuid.uuid4()}",
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
    async def _fake(*args, **kwargs):
        return {
            "label": label,
            "confidence": confidence,
            "reason": "deterministic test classification",
            "_model": "deepseek/deepseek-v4-flash",
            "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return _fake


async def _count_leads(db, identity_id: str) -> int:
    return (
        await db.execute(select(func.count()).select_from(Lead).where(
            Lead.identity_id == uuid.UUID(identity_id)
        ))
    ).scalars().one()


async def test_lead_created_exactly_once_for_same_identity(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    r1 = await client.post("/api/v1/events", json=_web_form(external_event_id="act-once-1"))
    r2 = await client.post("/api/v1/events", json=_web_form(external_event_id="act-once-2"))
    assert r1.status_code == 200 and r2.status_code == 200
    b1, b2 = r1.json(), r2.json()

    assert b1["lead_id"] == b2["lead_id"]
    assert b2["lead_op"] == "updated", f"expected updated, got {b2['lead_op']}"
    assert (await _count_leads(db_session, b1["identity_id"])) == 1


async def test_fallback_queue_on_unmatched_decision(client, db_session, monkeypatch):
    # Force a decision value that matches no routing rule (routing_rules_v1.json
    # only knows hot/warm/needs_review/cold).
    async def _fake_score_event(db, event, identity_id, interp_obj):
        return SimpleNamespace(
            id=uuid.uuid4(), score=40, decision="unmatched_for_test",
            features={"label": "other"},
        )

    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    monkeypatch.setattr("app.routers.events.score_event", _fake_score_event)

    resp = await client.post("/api/v1/events", json=_web_form())
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue"] == "unassigned"
    assert body["rule_matched"] == "fallback_no_rule"

    route = (
        await db_session.execute(select(Route))
    ).scalars().first()
    assert route is not None
    assert route.rule_matched == "fallback_no_rule"


async def test_transaction_atomicity_lead_and_route_together(client, db_session, monkeypatch):
    # route_lead raises AFTER create_or_update_lead flushed but BEFORE db.commit().
    async def _boom(db, lead, decision, label):
        raise RuntimeError("simulated route failure")

    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    monkeypatch.setattr("app.services.act.route_lead", _boom)

    with pytest.raises(RuntimeError):
        await client.post("/api/v1/events", json=_web_form())

    # The single-commit contract: no lead without a route — lead rolled back.
    assert (await db_session.execute(select(func.count()).select_from(Lead))).scalars().one() == 0


async def test_get_leads_returns_lead_after_creation(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    resp = await client.post("/api/v1/events", json=_web_form())
    assert resp.status_code == 200

    got = await client.get("/api/v1/leads")
    assert got.status_code == 200
    items = got.json()
    assert isinstance(items, list) and len(items) == 1
    row = items[0]
    for key in ("lead_id", "queue", "decision", "score"):
        assert row.get(key) is not None, key


async def test_get_lead_by_id_returns_full_detail(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()

    got = await client.get(f"/api/v1/leads/{posted['lead_id']}")
    assert got.status_code == 200
    body = got.json()
    for key in ("score", "decision", "score_features", "queue", "rule_matched", "sla_deadline"):
        assert body.get(key) is not None, key
    assert body["identity_id"] == posted["identity_id"]


async def test_get_lead_by_id_404_for_unknown(client):
    got = await client.get("/api/v1/leads/00000000-0000-0000-0000-000000000000")
    assert got.status_code == 404
    assert got.json() == {"error": "not_found"}