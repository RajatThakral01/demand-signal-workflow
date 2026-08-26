"""Integration tests — POST/GET /api/v1/events against a real test Postgres.

Covers PRD Error States (malformed vs schema-invalid), Flow 1 (create), Flow 2
(duplicate + edit), and the DB-level dedupe enforcement requirement.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import Event
from app.db.session import get_session_factory
from app.schemas.events import event_adapter
from app.services import ingest

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": "wf-0001",
        "received_at": NOW.isoformat(),
        "consent": True,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "company": "Analytical Engines",
        "message": "Interested in your pricing tiers",
    }
    payload.update(overrides)
    return payload


async def _count_events(db_session) -> int:
    result = await db_session.execute(select(func.count()).select_from(Event))
    return result.scalar_one()


# --- Error States: malformed JSON (400, NOT persisted) ------------------------
async def test_malformed_json_returns_400_and_not_persisted(client, db_session):
    resp = await client.post("/api/v1/events", content=b"{not valid json]")
    assert resp.status_code == 400
    assert resp.json()["error"] == "malformed_json"
    assert (await _count_events(db_session)) == 0


# --- Error States: valid JSON, fails schema (200 is_valid=false, persisted) ----
async def test_schema_invalid_returns_200_and_is_persisted(client, db_session):
    resp = await client.post(
        "/api/v1/events",
        json={"source": "mystery_source", "external_event_id": "x-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is False
    assert body["invalid_reason"]  # non-empty reason attached
    assert (await _count_events(db_session)) == 1


async def test_schema_invalid_identity_fields_persisted_isolated(client, db_session):
    # Missing email on a web_form is still structurally schema-invalid here when
    # we require identity fields; here we use a clearly invalid email to exercise
    # a schema validation failure that must NOT be dropped.
    resp = await client.post("/api/v1/events", json=_web_form(email="nope"))
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is False
    row = (await db_session.execute(select(Event))).scalars().first()
    assert row is not None
    assert row.is_valid is False
    assert row.invalid_reason


# --- Flow 1 step 1-2: valid event created -------------------------------------
async def test_valid_event_created(client, db_session, monkeypatch):
    from app.services import interpret as interp_svc
    async def _fake(*a, **kw):
        return {"label": "pricing_inquiry", "confidence": 0.9, "reason": "mocked", "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(interp_svc, "_call_llm", _fake)
    resp = await client.post("/api/v1/events", json=_web_form())
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is True
    assert body["duplicate"] is False
    assert (await _count_events(db_session)) == 1

    fetched = await client.get(f"/api/v1/events/{body['event_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["source"] == "web_form"
    assert fetched.json()["is_valid"] is True


# --- Flow 2: exact duplicate is a no-op ----------------------------------------
async def test_exact_duplicate_is_noop(client, db_session):
    first = await client.post("/api/v1/events", json=_web_form())
    second = await client.post("/api/v1/events", json=_web_form())
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert (
        await _count_events(db_session) == 1
    ), "a true duplicate must be a no-op (no new events row)"


# --- Flow 2: edited resubmission updates the row, no second row ----------------
async def test_edit_updates_row_and_marks_is_edit(client, db_session, monkeypatch):
    from app.services import interpret as interp_svc
    async def _fake(*a, **kw):
        return {"label": "pricing_inquiry", "confidence": 0.9, "reason": "mocked", "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(interp_svc, "_call_llm", _fake)
    original = _web_form(message="original message")
    await client.post("/api/v1/events", json=original)

    edited = _web_form(message="corrected message")
    resp = await client.post("/api/v1/events", json=edited)
    assert resp.status_code == 200
    assert resp.json()["is_edit"] is True
    assert resp.json()["duplicate"] is False
    assert (await _count_events(db_session)) == 1, "an edit must not create a 2nd row"

    row = (await db_session.execute(select(Event))).scalars().first()
    assert row.is_edit is True
    assert row.raw_payload["message"] == "corrected message"


# --- Concurrency: one events row no matter the race ----------------------------
async def test_concurrent_duplicates_create_one_row(db_engine):
    payload = _web_form(external_event_id="wf-concurrent")
    factory = get_session_factory()

    async def attempt():
        async with factory() as s:
            model = event_adapter.validate_python(payload)
            return await ingest.create_event(s, model, payload)

    results = await asyncio.gather(attempt(), attempt())
    statuses = sorted(r[1] for r in results)
    # Exactly one created, the other duplicate — never two rows.
    assert statuses == ["created", "duplicate"]

    async with factory() as s:
        count = await s.execute(select(func.count()).select_from(Event))
        assert count.scalar_one() == 1


# --- DB (not app) enforces uniqueness ------------------------------------------
async def test_db_constraint_blocks_duplicate_even_without_app_check(db_session):
    key = ingest.compute_dedupe_key("web_form", "wf-dbconstraint")
    base = dict(
        external_event_id="wf-dbconstraint",
        source="web_form",
        dedupe_key=key,
        payload_hash="h1",
        schema_version="1.0",
        raw_payload={},
        consent=False,
        received_at=NOW,
        is_valid=True,
    )
    db_session.add(Event(**base))
    await db_session.commit()

    # Bypass app-level dedupe entirely and insert the same dedupe_key directly:
    # the DB UNIQUE constraint must be the layer that blocks it.
    db_session.add(Event(**{**base, "payload_hash": "h2"}))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()