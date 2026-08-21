"""Integration tests — act pipeline (FR-6, FR-7, Phase 5).

POSTs events through the real API (mocked LLM) and asserts lead/route behavior:
exactly-once lead creation, fallback routing on unmatched decisions, the
single-commit atomicity contract, and the leads read endpoints.

Also covers the Phase 8 pre-work defect fix: routes.lead_id is UNIQUE (one route
per lead), an edited resubmission UPDATES the existing route in place (never a
second row), and concurrent route_lead calls cannot duplicate a route.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import Lead, Receipt, Route
from app.db.session import get_session_factory
from app.services import interpret
from app.services.act import route_lead

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
    monkeypatch.setattr("app.services.pipeline.score_event", _fake_score_event)

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


def _fake_call_llm_branching():
    """Return pricing_inquiry (hot) when the text mentions purchase, else other
    (warm) — used to make an edited resubmission produce a DIFFERENT decision."""

    async def _fake(*args, **kwargs):
        text = (kwargs.get("text") or "") if "text" in kwargs else (
            args[0] if args else ""
        )
        label = "pricing_inquiry" if "purchase" in text else "other"
        return {
            "label": label,
            "confidence": 0.9,
            "reason": "deterministic test classification",
            "_model": "deepseek/deepseek-v4-flash",
            "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return _fake


async def _count_routes_for_lead(db, lead_id: str) -> int:
    return (
        await db.execute(select(func.count()).select_from(Route).where(
            Route.lead_id == uuid.UUID(lead_id)
        ))
    ).scalars().one()


async def _count_route_receipts(db, route_id: str, action_type: str) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(Receipt).where(
                Receipt.entity_id == uuid.UUID(route_id),
                Receipt.entity_type == "route",
                Receipt.action_type == action_type,
            )
        )
    ).scalars().one()


async def test_edited_resubmission_updates_route_in_place(client, db_session, monkeypatch):
    """FR-2 edit re-runs interpret→score→act; the route must be UPDATED, not
    duplicated (routes.lead_id UNIQUE). Row count == 1 with the NEW decision."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm_branching())

    # Original: message mentions "purchase" -> pricing_inquiry -> hot -> sales_urgent.
    original = _web_form(external_event_id="edit-route-1")
    original["message"] = "We need to purchase your solution for our entire company urgently"
    r1 = await client.post("/api/v1/events", json=original)
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["queue"] == "sales_urgent"
    assert b1["rule_matched"] == "hot_any"
    lead_id = b1["lead_id"]

    # Edit resubmission: same external_event_id, no "purchase" -> other -> warm ->
    # sales_default (a DIFFERENT queue/rule than the original).
    edited = dict(original)
    edited["message"] = "We are casually exploring options and would like a general brochure"
    r2 = await client.post("/api/v1/events", json=edited)
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["is_edit"] is True
    assert b2["queue"] == "sales_default"
    assert b2["rule_matched"] == "warm_any"

    # Exactly ONE route row for the lead, reflecting the NEW decision.
    assert (await _count_routes_for_lead(db_session, lead_id)) == 1
    route = (
        await db_session.execute(select(Route).where(Route.lead_id == uuid.UUID(lead_id)))
    ).scalars().one()
    assert route.queue == "sales_default"
    assert route.rule_matched == "warm_any"

    # FR-9 receipts: the ORIGINAL creation wrote exactly one "routed" receipt; the
    # edit re-route (route updated in place) wrote exactly one "route_updated"
    # receipt. NO second "routed" receipt is written for the update call.
    route_id = b1["route_id"]
    assert route_id == b2["route_id"]  # same route row, updated in place
    assert (await _count_route_receipts(db_session, route_id, "routed")) == 1
    assert (await _count_route_receipts(db_session, route_id, "route_updated")) == 1


async def test_concurrent_route_lead_creates_one_route(db_session):
    """Two near-simultaneous route_lead calls for the same lead must yield exactly
    one routes row — the DB UNIQUE constraint (not app logic) is the guard."""
    # Seed a lead with NO route: create the lead directly, bypassing act().
    from app.db.models import Event, Identity

    now = datetime.now(timezone.utc)
    identity = Identity(primary_email="route-race@example.com", display_name="Race Test")
    db_session.add(identity)
    await db_session.flush()
    event = Event(
        id=uuid.uuid4(),
        external_event_id="route-race-ev",
        source="web_form",
        dedupe_key="route-race-key",
        payload_hash="route-race-hash",
        is_valid=True,
        schema_version="1.0",
        raw_payload={},
        consent=True,
        received_at=now,
    )
    db_session.add(event)
    await db_session.flush()
    lead = Lead(identity_id=identity.id, status="new", source_event_id=event.id)
    db_session.add(lead)
    await db_session.commit()
    lead_id = lead.id

    factory = get_session_factory()

    async def attempt(n):
        async with factory() as s:
            l = (await s.execute(select(Lead).where(Lead.id == lead_id))).scalars().one()
            route, op = await route_lead(s, l, "hot", "pricing_inquiry")
            await s.commit()
            return str(route.id)

    ids = await asyncio.gather(attempt(1), attempt(2))
    # Both succeeded and, critically, resolved to the SAME single route row.
    assert len(set(ids)) == 1

    async with factory() as s:
        count = (
            await s.execute(select(func.count()).select_from(Route).where(
                Route.lead_id == lead_id
            ))
        ).scalars().one()
    assert count == 1
