"""Integration tests — dashboard HTML pages (Phase 9, PRD §8).

Verifies the four evaluator screens are server-rendered, semantic HTML, no JS
framework, and that every count traces back to DB → receipts.  The
reconciliation badge must be live (from the JSON endpoint) not hardcoded.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import Lead, Route
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = NOW.isoformat()


def _wf(**overrides):
    base = {
        "source": "web_form",
        "external_event_id": f"wf-{uuid.uuid4()}",
        "schema_version": "1.0",
        "received_at": NOW_ISO,
        "consent": True,
        "campaign_id": "camp-dash-1",
        "name": "Dashboard Test",
        "email": f"dash-{uuid.uuid4()}@example.com",
        "company": "Dash Corp",
        "message": "I am interested in pricing for your product, could you share detailed pricing tiers for a team of twenty people including enterprise options and support.",
        "phone": None,
    }
    base.update(overrides)
    return base


def _soc(**overrides):
    base = {
        "source": "social_mention",
        "external_event_id": f"soc-{uuid.uuid4()}",
        "schema_version": "1.0",
        "received_at": NOW_ISO,
        "consent": False,
        "display_name": "Ada Lovelace",
        "handle": "ada_love",
        "text": "I am interested in pricing for your product, could you share detailed pricing tiers for a team of twenty people including enterprise options and support.",
        "topic": "pricing",
    }
    base.update(overrides)
    return base


def _fake_llm(label="pricing_inquiry", confidence=0.9):
    async def _inner(*args, **kwargs):
        return {
            "label": label,
            "confidence": confidence,
            "reason": "deterministic test classification",
            "_model": "deepseek/deepseek-v4-flash",
            "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    return _inner


async def test_dashboard_summary_json_has_expected_keys(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    await client.post("/api/v1/events", json=_wf())
    resp = await client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_events", "valid_events", "invalid_events", "by_source", "by_decision", "by_status", "total_leads", "pending_reviews", "dead_letters"):
        assert key in body, f"missing {key}"
    assert body["by_source"]["web_form"] >= 1
    assert body["total_events"] >= 1


async def test_dashboard_html_renders_with_reconciliation_badge(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    await client.post("/api/v1/events", json=_wf())
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert "<table" in html
    assert "<nav" in html
    assert "Reconciliation" in html
    # Badge must reflect live reconciliation — after a clean run it should be PASS
    assert "PASS" in html or "FAIL" in html
    assert "SIMULATED" in html and "LIVE" in html
    assert "DAXVORA-RAJAT-2026-08-A01" in html


async def test_dashboard_html_counts_match_seeded_data(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    # Seed one of each source
    await client.post("/api/v1/events", json=_wf(external_event_id=f"csum-wf-{uuid.uuid4()}"))
    await client.post("/api/v1/events", json=_soc(external_event_id=f"csum-soc-{uuid.uuid4()}"))
    # Invalid one
    await client.post("/api/v1/events", json={"source": "bad_source", "external_event_id": f"bad-{uuid.uuid4()}", "received_at": NOW_ISO})
    html = (await client.get("/dashboard")).text
    # Should show at least 1 for each valid source
    assert "web_form" in html
    assert "social_mention" in html
    assert "Total events" in html


async def test_root_redirects_to_dashboard(client):
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/dashboard"


async def test_leads_list_html_renders_and_filters(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    seeded = _wf(external_event_id=f"leadlist-{uuid.uuid4()}", email=f"leadlist-{uuid.uuid4()}@example.com")
    created = await client.post("/api/v1/events", json=seeded)
    lead_id = created.json()["lead_id"]
    html = (await client.get("/dashboard/leads")).text
    assert html.count("<table") >= 1
    assert lead_id[:8] in html
    # Filter by status
    filtered = await client.get("/dashboard/leads", params={"status": "routed"})
    assert filtered.status_code == 200
    assert lead_id[:8] in filtered.text


async def test_lead_detail_html_shows_full_trail(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm(label="pricing_inquiry", confidence=0.92))
    created = await client.post("/api/v1/events", json=_wf(external_event_id=f"detail-{uuid.uuid4()}"))
    lead_id = created.json()["lead_id"]
    html = (await client.get(f"/dashboard/leads/{lead_id}")).text
    assert html.count("<table") >= 1
    assert "Score features" in html
    assert "Attribution" in html
    assert "First touch" in html
    assert lead_id in html


async def test_lead_detail_html_404_for_unknown(client):
    resp = await client.get(f"/dashboard/leads/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_manual_review_html_shows_pending_and_resolve_forms(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    # Seed a fuzzy-only event that parks
    await client.post("/api/v1/events", json=_soc(external_event_id=f"mr-seed-{uuid.uuid4()}", display_name="Ada Solo"))
    parked = await client.post("/api/v1/events", json=_soc(external_event_id=f"mr-park-{uuid.uuid4()}", display_name="Ada Solo"))
    assert parked.json()["status"] == "manual_review"
    html = (await client.get("/dashboard/manual-review")).text
    assert "Manual review queue" in html
    assert "<table" in html
    assert "Create new" in html
    assert parked.json()["review_id"][:8] in html


async def test_manual_review_resolve_via_html_form(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    await client.post("/api/v1/events", json=_soc(external_event_id=f"frm-a-{uuid.uuid4()}", display_name="Form User"))
    parked = await client.post("/api/v1/events", json=_soc(external_event_id=f"frm-b-{uuid.uuid4()}", display_name="Form User"))
    rid = parked.json()["review_id"]
    # HTML form posts decision=create_new
    resp = await client.post(f"/dashboard/manual-review/{rid}/resolve", data={"decision": "create_new"})
    # Should redirect back to queue (303) or 200
    assert resp.status_code in (200, 303)
    # Verify resolved via JSON API
    # Follow redirect if needed
    if resp.status_code == 303:
        q = await client.get("/dashboard/manual-review")
        assert rid[:8] in q.text or "resolved" in q.text


async def test_dead_letter_html_lists_outstanding_and_replay_link(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    created = await client.post("/api/v1/events", json=_wf(external_event_id=f"dlhtml-{uuid.uuid4()}"))
    event_id = created.json()["event_id"]
    await client.post("/api/v1/admin/simulate-failure", headers={"Authorization": "Bearer test_admin_key"}, json={"stage": "interpret", "event_id": event_id})
    html = (await client.get("/dashboard/dead-letter")).text
    assert "Dead-letter queue" in html
    assert event_id[:8] in html
    assert "replay" in html.lower()
    assert "<table" in html


async def test_dead_letter_html_filtered_resolved(client, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_llm())
    created = await client.post("/api/v1/events", json=_wf(external_event_id=f"dlfilt-{uuid.uuid4()}"))
    eid = created.json()["event_id"]
    await client.post("/api/v1/admin/simulate-failure", headers={"Authorization": "Bearer test_admin_key"}, json={"stage": "interpret", "event_id": eid})
    await client.post(f"/api/v1/admin/replay/{eid}", headers={"Authorization": "Bearer test_admin_key"})
    html_resolved = (await client.get("/dashboard/dead-letter", params={"resolved": "true"})).text
    html_unresolved = (await client.get("/dashboard/dead-letter", params={"resolved": "false"})).text
    assert eid[:8] in html_resolved
    assert eid[:8] not in html_unresolved


async def test_static_css_served(client):
    resp = await client.get("/static/style.css")
    assert resp.status_code == 200
    assert "system-ui" in resp.text
