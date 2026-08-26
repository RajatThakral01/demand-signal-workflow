"""Proof that Fix 2 is needed: only first validation error is recorded."""
from sqlalchemy import select
from app.db.models import Event

async def test_fix2_all_validation_errors_recorded(client, db_session):
    """Payload with two independent failures (missing external_event_id + invalid email) must record BOTH."""
    payload = {
        "source": "web_form",
        # external_event_id missing -> 1st error
        "received_at": "2026-08-20T12:00:00Z",
        "email": "not-an-email",  # 2nd error
        "message": "hi",
    }
    resp = await client.post("/api/v1/events", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is False
    reason = body["invalid_reason"]
    # Should contain both problems, not just one
    # Pydantic errors for these are like "Field required" and "value is not a valid email address"
    assert "not-an-email" in reason or "email" in reason.lower(), f"email error missing in {reason!r}"
    # Need at least two distinct error segments (semicolon-separated)
    parts = [p.strip() for p in reason.split(";") if p.strip()]
    assert len(parts) >= 2, f"expected >=2 error messages joined, got {reason!r}"
    # Also DB persisted
    row = (await db_session.execute(select(Event).where(Event.id == body["event_id"]))).scalars().first()
    assert row.invalid_reason == reason

async def test_fix2_single_error_still_identical(client):
    """Single-error payload should look identical to previous behavior (one message, no extra semicolons)."""
    payload = {
        "source": "web_form",
        "external_event_id": "single-err-001",
        "received_at": "2026-08-20T12:00:00Z",
        "email": "not-an-email",
        "message": "hi",
    }
    resp = await client.post("/api/v1/events", json=payload)
    assert resp.json()["is_valid"] is False
    reason = resp.json()["invalid_reason"]
    # Should be single message, not empty, and not contain spurious semicolon duplication
    assert reason
    assert ";" not in reason or reason.count(";") == 0  # single error => no separator

