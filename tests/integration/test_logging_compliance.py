"""Logging compliance tests (FR-10, Phase 7b).

Verifies every service structlog call carries the seven required fields
(input_id, decision, reason, action, result, error, timing_ms) and that PII is
redacted (SHA-256-hashed) before it reaches the log output.
"""

import hashlib
import uuid
from datetime import datetime, timezone

from structlog.testing import capture_logs

from app.logging import _pii_redactor
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

SERVICE_ACTIONS = {
    "event_created", "interpreted", "scored",
    "lead_created", "routed", "attributed_created",
}
REQUIRED_FIELDS = {"input_id", "decision", "reason", "action", "result", "error", "timing_ms"}


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": f"log-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "consent": True,
        "name": "Logging Test",
        "email": f"log-{uuid.uuid4()}@example.com",
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


async def test_log_contains_all_seven_required_fields(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    with capture_logs() as log_entries:
        resp = await client.post("/api/v1/events", json=_web_form())
        assert resp.status_code == 200

    service_entries = [e for e in log_entries if e.get("action") in SERVICE_ACTIONS]
    assert len(service_entries) >= 3, f"Expected >=3 service entries, got {len(service_entries)}"
    for entry in service_entries:
        missing = REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"Log entry missing fields {missing}: {entry}"
    return service_entries


async def test_log_contains_no_raw_pii(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    payload = _web_form(email="pii-test@example.com", name="PII TestUser")
    with capture_logs(processors=[_pii_redactor]) as log_entries:
        resp = await client.post("/api/v1/events", json=payload)
        assert resp.status_code == 200

    all_log_text = str(log_entries)
    assert "pii-test@example.com" not in all_log_text, \
        f"Raw email found in logs: {all_log_text}"
    assert "PII TestUser" not in all_log_text, \
        f"Raw name found in logs: {all_log_text}"

    expected_hash = "sha256:" + hashlib.sha256(b"pii-test@example.com").hexdigest()[:16]
    assert expected_hash in all_log_text, \
        f"Expected hashed email not found. Redaction may not have run. Logs: {all_log_text}"
    return expected_hash, log_entries