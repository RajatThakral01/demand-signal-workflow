"""Proof that Fix 3 lowered threshold: short buying-intent now calls LLM."""
from unittest.mock import AsyncMock
from app.services import interpret

async def test_fix3_short_buying_intent_not_skipped_calls_llm(client, monkeypatch):
    """3-word 'want a quote' and 7-word 'i am interested in buying your product' must NOT be skipped.
    Mirrors the existing spy-assert-never-invoked test but inverted."""
    spy = AsyncMock(return_value={
        "label": "pricing_inquiry", "confidence": 0.9, "reason": "buying intent",
        "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    })
    monkeypatch.setattr(interpret, "_call_llm", spy)

    # 3-word realistic intent
    payload = {
        "source": "web_form",
        "external_event_id": "fix3-3words",
        "received_at": "2026-08-20T12:00:00Z",
        "consent": True,
        "name": "Buyer",
        "email": "buyer@example.com",
        "company": "Acme",
        "message": "want a quote",
    }
    resp = await client.post("/api/v1/events", json=payload)
    assert resp.status_code == 200
    # Must have called LLM (not skipped)
    spy.assert_called_once()
    assert resp.json()["label"] == "pricing_inquiry"

    # Reset and test 7-word intent
    spy.reset_mock()
    spy.return_value = {"label": "pricing_inquiry", "confidence": 0.92, "reason": "7 words", "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    payload2 = {
        "source": "web_form",
        "external_event_id": "fix3-7words",
        "received_at": "2026-08-20T12:00:00Z",
        "consent": True,
        "name": "Buyer2",
        "email": "buyer2@example.com",
        "company": "Acme",
        "message": "i am interested in buying your product",
    }
    resp2 = await client.post("/api/v1/events", json=payload2)
    assert resp2.status_code == 200
    spy.assert_called_once()
    assert resp2.json()["label"] == "pricing_inquiry"

async def test_fix3_single_word_still_skipped(client, monkeypatch):
    """Pure noise like 'hi' (1 word <2) must still be skipped."""
    spy = AsyncMock(return_value={
        "label": "pricing_inquiry", "confidence": 0.9, "reason": "should not be called",
        "_model": "deepseek/deepseek-v4-flash", "_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    })
    monkeypatch.setattr(interpret, "_call_llm", spy)
    payload = {
        "source": "web_form",
        "external_event_id": "fix3-noise",
        "received_at": "2026-08-20T12:00:00Z",
        "consent": True,
        "name": "Noisy",
        "email": "noisy@example.com",
        "company": "Acme",
        "message": "hi",
    }
    resp = await client.post("/api/v1/events", json=payload)
    assert resp.status_code == 200
    spy.assert_not_called()
    assert resp.json()["label"] == "unknown"
