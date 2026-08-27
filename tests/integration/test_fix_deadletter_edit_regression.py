"""Regression for live-found bug: edit of a dead-lettered event must still return 202, not 500.

Reproduction: submit an event with real text (needs LLM) while provider is
failing (AuthenticationError, not retryable), so it dead-letters (202).
Then submit an EDIT of that same event (same external_event_id, different
payload) while provider is STILL failing. Before the fix, the second
submission hit the duplicate_suppressed branch in classify_event (which
suppressed the second DeadLetterQueue row due to partial unique index) and
incorrectly let the original provider exception propagate instead of
re-raising InterpretError, resulting in an unhandled 500. After the fix,
both branches raise InterpretError identically, so the router returns 202.

This test must fail against the buggy code (second edit returns 500) and
pass after the fix (second edit returns 202 dead_letter).
"""

import openai
import httpx
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.services import interpret

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _auth_error():
    return openai.AuthenticationError(
        message="invalid key",
        response=httpx.Response(
            status_code=401,
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
        ),
        body=None,
    )


async def test_edit_of_dead_lettered_event_still_returns_202_not_500(client, monkeypatch):
    # Force provider to always fail with non-retryable 401
    monkeypatch.setattr(interpret.settings, "retry_max_attempts", 1)
    monkeypatch.setattr(interpret.settings, "retry_base_delay_ms", 1)
    monkeypatch.setattr(interpret, "_call_llm", AsyncMock(side_effect=_auth_error()))

    base_payload = {
        "source": "web_form",
        "external_event_id": f"edit-dl-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "consent": True,
        "name": "Live Test",
        "email": f"live-{uuid.uuid4()}@example.com",
        "company": "Acme",
        "message": "Our team of ten engineers is evaluating your platform for onboarding and integration after the pilot and we have several questions about compliance and security requirements that need detailed answers",
    }

    first = await client.post("/api/v1/events", json=base_payload)
    assert first.status_code == 202, f"first dead-letter should be 202, got {first.status_code} {first.text}"
    assert first.json()["status"] == "dead_letter"
    assert first.json()["stage"] == "interpret"
    event_id = first.json()["event_id"]

    # Edit same event (same external_event_id, different payload) while provider still failing
    edited = {**base_payload, "message": base_payload["message"] + " plus an edit with more details about enterprise SSO and SAML support for the second attempt"}
    second = await client.post("/api/v1/events", json=edited)
    # Must be 202 dead_letter, not 500
    assert second.status_code == 202, f"edit of dead-lettered event should still be 202, got {second.status_code} {second.text}"
    body = second.json()
    assert body["status"] == "dead_letter"
    assert body["stage"] == "interpret"
    assert body["event_id"] == event_id
    # is_edit should be true
    assert body["is_edit"] is True
