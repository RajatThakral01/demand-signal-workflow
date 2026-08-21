"""Integration tests — receipts reconciliation (FR-9, Phase 7).

Verifies that every mutating action writes a receipt and that the reconciliation
endpoint detects any mutation committed without one.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import Event, Receipt
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": f"rec-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "consent": True,
        "name": "Receipt Test",
        "email": f"rec-{uuid.uuid4()}@example.com",
        "company": "Test Corp",
        "message": (
            "I'd like to learn about your pricing for our team of 20 people"
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


async def _count_receipts(db, action_type: str) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(Receipt).where(
                Receipt.action_type == action_type
            )
        )
    ).scalars().one()


async def test_reconciliation_variance_zero_on_full_seeded_run(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())

    # (a) valid event (happy path) -> event, identity, lead, route, touch
    payload_a = _web_form(external_event_id="seed-a", email="seed@example.com")
    a = await client.post("/api/v1/events", json=payload_a)
    assert a.status_code == 200
    # (b) same event again -> duplicate (no-op, no new receipts)
    dup = await client.post("/api/v1/events", json=payload_a)
    assert dup.status_code == 200 and dup.json()["duplicate"] is True
    # (c) invalid event (valid JSON, bad source) -> event_rejected
    invalid = {
        "source": "mystery_source",
        "external_event_id": "seed-bad",
        "received_at": NOW.isoformat(),
        "message": "garbage",
    }
    bad = await client.post("/api/v1/events", json=invalid)
    assert bad.status_code == 200 and bad.json()["is_valid"] is False
    # (d) second valid event, same email -> lead_updated (same identity)
    d = await client.post("/api/v1/events",
                          json=_web_form(external_event_id="seed-2", email="seed@example.com"))
    assert d.status_code == 200

    resp = await client.get("/api/v1/dashboard/reconciliation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] == "ok", body
    assert body["total_variance"] == 0, body
    for entry in body["reconciliation"]:
        assert entry["variance"] == 0, entry
    return body


async def test_reconciliation_detects_mismatch_when_receipt_missing(client, db_session, monkeypatch):
    # Bypass the service layer: insert an event directly so NO receipt is written.
    ev = Event(
        id=uuid.uuid4(),
        external_event_id="direct-insert",
        source="web_form",
        dedupe_key="direct-insert-key",
        payload_hash="direct-hash",
        is_valid=True,
        is_edit=False,
        schema_version="1.0",
        raw_payload={},
        consent=False,
        received_at=NOW,
    )
    db_session.add(ev)
    await db_session.commit()

    resp = await client.get("/api/v1/dashboard/reconciliation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] == "mismatch", body
    assert body["total_variance"] > 0, body


async def test_receipt_rows_written_for_every_action(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    resp = await client.post("/api/v1/events", json=_web_form())
    assert resp.status_code == 200

    present = {
        "event_created", "identity_created", "interpreted", "scored",
        "lead_created", "routed", "attributed_created",
    }
    for action in present:
        assert (await _count_receipts(db_session, action)) == 1, action

    # These must NOT have happened in this single-event run.
    absent = {"event_edited", "event_rejected", "lead_updated"}
    for action in absent:
        assert (await _count_receipts(db_session, action)) == 0, action


async def test_duplicate_event_writes_no_receipt(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    payload = _web_form()
    r1 = await client.post("/api/v1/events", json=payload)
    assert r1.status_code == 200
    r2 = await client.post("/api/v1/events", json=payload)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True

    assert (await _count_receipts(db_session, "event_created")) == 1
    assert (await _count_receipts(db_session, "event_edited")) == 0


async def test_edit_event_writes_event_edited_receipt(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    original = _web_form(external_event_id="edit-same")
    r1 = await client.post("/api/v1/events", json=original)
    assert r1.status_code == 200

    edited = dict(original)
    edited["message"] = "Completely different message to trigger edit detection now"
    r2 = await client.post("/api/v1/events", json=edited)
    assert r2.status_code == 200 and r2.json()["is_edit"] is True

    assert (await _count_receipts(db_session, "event_created")) == 1
    assert (await _count_receipts(db_session, "event_edited")) == 1


async def test_create_then_edit_keeps_reconciliation_variance_zero(client, db_session, monkeypatch):
    """Regression test for the events_created reconciliation mismatch defect.

    A previously-created valid event that is later edited flips is_edit=True. The
    OLD events_created dashboard query filtered on is_edit=False, so the row dropped
    out of the created count (0) while the immutable event_created receipt stayed at
    1 -> variance 1, mismatch. The fix counts events_created by is_valid only.
    """
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())

    # Create one valid event, then confirm reconciliation is clean.
    original = _web_form(external_event_id="regress-create-edit")
    r1 = await client.post("/api/v1/events", json=original)
    assert r1.status_code == 200

    body1 = (await client.get("/api/v1/dashboard/reconciliation")).json()
    assert body1["overall_status"] == "ok", body1
    assert body1["total_variance"] == 0, body1
    ev1 = [e for e in body1["reconciliation"] if e["entity"] == "events_created"][0]
    assert ev1["variance"] == 0, ev1

    # Edit the SAME event (same external_event_id, different message).
    edited = dict(original)
    edited["message"] = "Completely different message to trigger edit detection now"
    r2 = await client.post("/api/v1/events", json=edited)
    assert r2.status_code == 200 and r2.json()["is_edit"] is True

    body2 = (await client.get("/api/v1/dashboard/reconciliation")).json()
    assert body2["overall_status"] == "ok", body2
    assert body2["total_variance"] == 0, body2
    ev_created = [e for e in body2["reconciliation"] if e["entity"] == "events_created"][0]
    ev_edited = [e for e in body2["reconciliation"] if e["entity"] == "events_edited"][0]
    assert ev_created["variance"] == 0, ev_created
    assert ev_edited["variance"] == 0, ev_edited