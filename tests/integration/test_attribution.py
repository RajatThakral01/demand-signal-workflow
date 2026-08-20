"""Integration tests — attribution first/last-touch (FR-8, Phase 6).

POSTs events through the real API (mocked LLM) and asserts the
attribution_touches row: first-touch immutability, last-touch tracking,
out-of-order delivery resolution by received_at (not arrival order), tie
determinism, edit-in-place (no duplicate row), and exposure via the ingest
response and the leads read endpoints.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import AttributionTouch, Event
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(ext_id: str, received_at: str, email="attr@example.com",
              campaign_id=None):
    payload = {
        "source": "web_form",
        "external_event_id": ext_id,
        "received_at": received_at,
        "consent": True,
        "name": "Attribution Test",
        "email": email,
        "company": "Test Corp",
        "message": "I'd like to learn about your pricing for our team of 20",
    }
    if campaign_id:
        payload["campaign_id"] = campaign_id
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


async def _fetch_touch(db, identity_id: str) -> AttributionTouch | None:
    return (
        await db.execute(
            select(AttributionTouch)
            .where(AttributionTouch.identity_id == uuid.UUID(identity_id))
            .execution_options(populate_existing=True)  # fresh read, not identity-map cache
        )
    ).scalars().first()


async def _event_db_id(db, ext_id: str) -> uuid.UUID:
    return (
        await db.execute(select(Event).where(Event.external_event_id == ext_id))
    ).scalars().one().id


async def test_first_and_last_touch_set_on_first_event(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    resp = await client.post("/api/v1/events",
                             json=_web_form("attr-1", "2026-08-20T09:00:00+00:00"))
    assert resp.status_code == 200
    event_id = uuid.UUID(resp.json()["event_id"])
    event = (await db_session.execute(select(Event).where(Event.id == event_id))).scalars().one()

    touch = await _fetch_touch(db_session, resp.json()["identity_id"])
    assert touch is not None
    assert touch.first_touch_event_id == event_id
    assert touch.last_touch_event_id == event_id
    assert touch.first_touch_at == event.received_at
    assert touch.last_touch_at == event.received_at
    assert touch.first_touch_source == "web_form"
    assert touch.last_touch_source == "web_form"


async def test_multi_event_sequence_first_never_changes_last_tracks(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    seq = [
        ("attr-seq-1", "2026-08-20T08:00:00+00:00"),
        ("attr-seq-2", "2026-08-20T10:00:00+00:00"),
        ("attr-seq-3", "2026-08-20T12:00:00+00:00"),
    ]
    identity_id = None
    for ext, at in seq:
        r = await client.post("/api/v1/events", json=_web_form(ext, at))
        assert r.status_code == 200
        identity_id = r.json()["identity_id"]

    count = (
        await db_session.execute(select(func.count()).select_from(AttributionTouch).where(
            AttributionTouch.identity_id == uuid.UUID(identity_id)
        ))
    ).scalars().one()
    assert count == 1

    touch = await _fetch_touch(db_session, identity_id)
    assert touch is not None
    assert touch.first_touch_at == datetime.fromisoformat("2026-08-20T08:00:00+00:00")
    assert touch.last_touch_at == datetime.fromisoformat("2026-08-20T12:00:00+00:00")
    assert touch.first_touch_event_id == await _event_db_id(db_session, "attr-seq-1")


async def test_out_of_order_delivery_resolves_by_received_at_not_arrival(client, db_session, monkeypatch):
    """Critical: event_C has the EARLIEST received_at but arrives THIRD."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    a = await client.post("/api/v1/events",
                          json=_web_form("attr-ooo-A", "2026-08-20T10:00:00+00:00"))
    b = await client.post("/api/v1/events",
                          json=_web_form("attr-ooo-B", "2026-08-20T12:00:00+00:00"))
    c = await client.post("/api/v1/events",
                          json=_web_form("attr-ooo-C", "2026-08-20T07:00:00+00:00"))
    assert a.status_code == b.status_code == c.status_code == 200
    identity_id = a.json()["identity_id"]

    touch = await _fetch_touch(db_session, identity_id)
    assert touch is not None
    assert touch.first_touch_at == datetime.fromisoformat("2026-08-20T07:00:00+00:00")
    assert touch.last_touch_at == datetime.fromisoformat("2026-08-20T12:00:00+00:00")
    assert touch.first_touch_event_id == await _event_db_id(db_session, "attr-ooo-C")


async def test_tie_on_received_at_does_not_replace_last_touch(client, db_session, monkeypatch):
    """Equal timestamps: the first-processed event wins both first and last touch."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    a = await client.post("/api/v1/events",
                          json=_web_form("attr-tie-A", "2026-08-20T10:00:00+00:00"))
    b = await client.post("/api/v1/events",
                          json=_web_form("attr-tie-B", "2026-08-20T10:00:00+00:00"))
    assert a.status_code == b.status_code == 200

    touch = await _fetch_touch(db_session, a.json()["identity_id"])
    assert touch is not None
    assert touch.last_touch_event_id == uuid.UUID(a.json()["event_id"])
    assert touch.first_touch_event_id == uuid.UUID(a.json()["event_id"])


async def test_edit_updates_touch_in_place_not_duplicated(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    first = await client.post("/api/v1/events",
                              json=_web_form("attr-edit-1", "2026-08-20T09:00:00+00:00"))
    assert first.status_code == 200
    identity_id = first.json()["identity_id"]
    original_event_id = uuid.UUID(first.json()["event_id"])

    touch = await _fetch_touch(db_session, identity_id)
    assert touch is not None
    assert touch.first_touch_source == "web_form"
    assert touch.first_touch_campaign_id is None

    # Same external_event_id, different message + a campaign -> edit path.
    edit_payload = _web_form("attr-edit-1", "2026-08-20T09:00:00+00:00",
                             campaign_id="edit-camp")
    edit_payload["message"] = "Different message to trigger edit detection"
    edited = await client.post("/api/v1/events", json=edit_payload)
    assert edited.status_code == 200
    assert edited.json()["is_edit"] is True

    count = (
        await db_session.execute(select(func.count()).select_from(AttributionTouch).where(
            AttributionTouch.identity_id == uuid.UUID(identity_id)
        ))
    ).scalars().one()
    assert count == 1  # no duplicate touch row

    touch = await _fetch_touch(db_session, identity_id)
    assert touch.first_touch_event_id == original_event_id  # unchanged
    assert touch.last_touch_event_id == original_event_id
    # Denormalized fields updated in place; received_at untouched.
    assert touch.first_touch_campaign_id == "edit-camp"
    assert touch.first_touch_at == datetime.fromisoformat("2026-08-20T09:00:00+00:00")


async def test_attribution_touch_id_in_ingest_response(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    resp = await client.post("/api/v1/events",
                             json=_web_form("attr-rsp-1", "2026-08-20T09:00:00+00:00"))
    assert resp.status_code == 200
    touch_id = resp.json().get("attribution_touch_id")
    assert touch_id
    uuid.UUID(touch_id)  # must be a valid UUID string


async def test_get_lead_includes_attribution_fields(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events",
                                json=_web_form("attr-lead-1", "2026-08-20T09:00:00+00:00"))).json()

    got = await client.get(f"/api/v1/leads/{posted['lead_id']}")
    assert got.status_code == 200
    body = got.json()
    for key in ("first_touch_at", "first_touch_source", "last_touch_at", "last_touch_source"):
        assert body.get(key) is not None, key