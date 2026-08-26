"""Proof that Fix 10 is needed: simulate-failure should not create duplicate unresolved DLQ rows."""
from sqlalchemy import select, func
from app.db.models import DeadLetterQueue

async def test_fix10_simulate_failure_twice_returns_200_then_409(client, db_session, monkeypatch):
    from app.services import interpret
    async def _fake(*a, **kw):
        return {"label": "pricing_inquiry", "confidence": 0.9, "reason": "mocked", "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(interpret, "_call_llm", _fake)

    # Create a valid event
    payload = {
        "source": "web_form",
        "external_event_id": "fix10-dedup-001",
        "received_at": "2026-08-20T12:00:00Z",
        "consent": True,
        "name": "Fix10",
        "email": "fix10@example.com",
        "company": "Acme",
        "message": "This is a long enough message to be classified and create a lead for fix10 testing",
    }
    created = await client.post("/api/v1/events", json=payload)
    assert created.status_code == 200
    event_id = created.json()["event_id"]

    # First simulate-failure should succeed
    r1 = await client.post("/api/v1/admin/simulate-failure", json={"stage": "interpret", "event_id": event_id}, headers={"Authorization": "Bearer test_admin_key"})
    assert r1.status_code == 200
    assert r1.json()["status"] == "dead_lettered"

    # Second simulate-failure on same event should be 409 already_dead_lettered
    r2 = await client.post("/api/v1/admin/simulate-failure", json={"stage": "interpret", "event_id": event_id}, headers={"Authorization": "Bearer test_admin_key"})
    assert r2.status_code == 409, f"expected 409, got {r2.status_code} {r2.text}"
    body = r2.json()
    # FastAPI wraps detail as {"detail": {"error": ...}} or flat {"error": ...}
    err = body.get("error") or body.get("detail", {}).get("error") if isinstance(body.get("detail"), dict) else body.get("detail")
    # Fallback check substring
    assert "already_dead_lettered" in str(body), f"expected already_dead_lettered in {body}"

    # Exactly one unresolved DLQ row
    count = (await db_session.execute(select(func.count()).select_from(DeadLetterQueue).where(DeadLetterQueue.event_id == event_id, DeadLetterQueue.resolved.is_(False)))).scalar_one()
    assert count == 1, f"expected exactly 1 unresolved DLQ row, got {count}"
    total = (await db_session.execute(select(func.count()).select_from(DeadLetterQueue).where(DeadLetterQueue.event_id == event_id))).scalar_one()
    assert total == 1

async def test_fix10_concurrent_simulate_failure_creates_one_row(client, db_session, monkeypatch):
    """Two concurrent simulate-failure calls for same event must yield exactly one unresolved row and 200+409."""
    import asyncio
    from app.services import interpret
    async def _fake(*a, **kw):
        return {"label": "pricing_inquiry", "confidence": 0.9, "reason": "mocked", "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(interpret, "_call_llm", _fake)
    payload = {
        "source": "web_form",
        "external_event_id": "fix10-conc-001",
        "received_at": "2026-08-20T12:00:00Z",
        "consent": True,
        "name": "Fix10Conc",
        "email": "fix10conc@example.com",
        "company": "Acme",
        "message": "Concurrent simulate-failure test message long enough to be classified",
    }
    created = await client.post("/api/v1/events", json=payload)
    assert created.status_code == 200
    event_id = created.json()["event_id"]

    # Fire two simulate-failure calls concurrently (same event, separate DB sessions via dependency)
    results = await asyncio.gather(
        client.post("/api/v1/admin/simulate-failure", json={"stage": "interpret", "event_id": event_id}, headers={"Authorization": "Bearer test_admin_key"}),
        client.post("/api/v1/admin/simulate-failure", json={"stage": "interpret", "event_id": event_id}, headers={"Authorization": "Bearer test_admin_key"}),
        return_exceptions=False,
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], f"expected [200,409] got {statuses} bodies {[r.text for r in results]}"
    # Exactly one unresolved row, not two
    count = (await db_session.execute(select(func.count()).select_from(DeadLetterQueue).where(DeadLetterQueue.event_id == event_id, DeadLetterQueue.resolved.is_(False)))).scalar_one()
    assert count == 1, f"expected exactly 1 unresolved DLQ row after concurrent race, got {count}"
    total = (await db_session.execute(select(func.count()).select_from(DeadLetterQueue).where(DeadLetterQueue.event_id == event_id))).scalar_one()
    assert total == 1

async def test_fix10_after_resolved_can_simulate_again(client, db_session, monkeypatch):
    """After replay resolves the DLQ, simulate-failure should be allowed again (new unresolved after resolved is ok)."""
    from app.services import interpret
    async def _fake(*a, **kw):
        return {"label": "pricing_inquiry", "confidence": 0.9, "reason": "mocked", "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(interpret, "_call_llm", _fake)
    payload = {
        "source": "web_form",
        "external_event_id": "fix10-resolved-001",
        "received_at": "2026-08-20T12:00:00Z",
        "consent": True,
        "name": "Fix10b",
        "email": "fix10b@example.com",
        "company": "Acme",
        "message": "Another long enough message for fix10 resolved test",
    }
    created = await client.post("/api/v1/events", json=payload)
    event_id = created.json()["event_id"]
    await client.post("/api/v1/admin/simulate-failure", json={"stage": "interpret", "event_id": event_id}, headers={"Authorization": "Bearer test_admin_key"})
    # Replay to resolve
    replay = await client.post(f"/api/v1/admin/replay/{event_id}", headers={"Authorization": "Bearer test_admin_key"})
    assert replay.status_code == 200
    # Now simulate again should be allowed (previous DLQ resolved)
    r2 = await client.post("/api/v1/admin/simulate-failure", json={"stage": "interpret", "event_id": event_id}, headers={"Authorization": "Bearer test_admin_key"})
    assert r2.status_code == 200
