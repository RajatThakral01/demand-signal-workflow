"""Unit tests — LLM provider abstraction (openrouter vs groq).

Verifies the branching in app/services/interpret.py _InterpretClient.get_client()
and _call_llm temperature handling, without making any real network call.
"""

import types
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.services import interpret
from app.services.interpret import _InterpretClient, GROQ_BASE_URL, OPENROUTER_BASE_URL, OPENROUTER_REFERRER


def _fake_settings(provider="openrouter", openrouter_key="test-or-key", groq_key="test-groq-key", model="deepseek/deepseek-v4-flash"):
    return types.SimpleNamespace(
        llm_provider=provider,
        openrouter_api_key=openrouter_key,
        groq_api_key=groq_key,
        classification_model=model,
    )


def test_openrouter_uses_correct_base_url_key_and_headers(monkeypatch):
    fake_settings = _fake_settings(provider="openrouter", openrouter_key="or-key-123")
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, base_url=None, api_key=None, default_headers=None):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["headers"] = default_headers

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    client = _InterpretClient(settings_=fake_settings)
    instance = client.get_client()

    assert captured["base_url"] == OPENROUTER_BASE_URL
    assert captured["api_key"] == "or-key-123"
    assert captured["headers"] == {"HTTP-Referer": OPENROUTER_REFERRER, "X-Title": "demand-signal-workflow"}
    # Second call returns same cached client
    assert client.get_client() is instance


def test_groq_uses_correct_base_url_key_and_no_openrouter_headers(monkeypatch):
    fake_settings = _fake_settings(provider="groq", groq_key="groq-key-456")
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, base_url=None, api_key=None, default_headers=None):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["headers"] = default_headers

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    client = _InterpretClient(settings_=fake_settings)
    client.get_client()

    assert captured["base_url"] == GROQ_BASE_URL
    assert captured["api_key"] == "groq-key-456"
    # Groq must NOT receive OpenRouter-specific attribution headers
    assert captured["headers"] is None or captured["headers"] == {} or "HTTP-Referer" not in (captured["headers"] or {})
    assert captured["headers"] is None or "X-Title" not in (captured["headers"] or {})


def test_groq_missing_api_key_raises_at_call_time(monkeypatch):
    fake_settings = _fake_settings(provider="groq", groq_key="")
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock())

    client = _InterpretClient(settings_=fake_settings)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not configured"):
        client.get_client()


def test_openrouter_missing_api_key_raises_at_call_time(monkeypatch):
    fake_settings = _fake_settings(provider="openrouter", openrouter_key="")
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock())

    client = _InterpretClient(settings_=fake_settings)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not configured"):
        client.get_client()


def test_invalid_provider_raises_clear_error(monkeypatch):
    fake_settings = _fake_settings(provider="invalid-provider")
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock())

    client = _InterpretClient(settings_=fake_settings)
    with pytest.raises(RuntimeError, match="Unknown llm_provider 'invalid-provider'"):
        client.get_client()


async def test_call_llm_temperature_is_zero_for_openrouter(monkeypatch):
    # Arrange: mock client to capture temperature
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        # Return a minimal successful response shape for _call_llm parsing
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content='{"label":"other","confidence":0.5,"reason":"test"}'))]
        mock_resp.usage = None
        return mock_resp

    fake_client = MagicMock()
    fake_client.chat.completions.create = fake_create

    fake_settings = _fake_settings(provider="openrouter", model="deepseek/deepseek-v4-flash")
    # Patch _InterpretClient.get_client to return our fake_client
    monkeypatch.setattr(interpret._client_holder, "_client", fake_client)
    # Also ensure settings used inside _call_llm sees openrouter
    monkeypatch.setattr(interpret.settings, "llm_provider", "openrouter", raising=False)
    monkeypatch.setattr(interpret.settings, "classification_model", "deepseek/deepseek-v4-flash", raising=False)

    # Need to ensure _client_holder.get_client returns fake_client without re-creating
    # Patch the holder's get_client to return fake_client directly
    monkeypatch.setattr(interpret._client_holder, "get_client", lambda: fake_client)

    await interpret._call_llm("some long enough text for classification that is definitely more than two tokens")

    assert captured["temperature"] == 0
    assert captured["model"] == "deepseek/deepseek-v4-flash"


async def test_call_llm_temperature_is_1e8_for_groq(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content='{"label":"other","confidence":0.5,"reason":"test"}'))]
        mock_resp.usage = None
        return mock_resp

    fake_client = MagicMock()
    fake_client.chat.completions.create = fake_create

    # Explicitly set provider to groq for this call
    monkeypatch.setattr(interpret.settings, "llm_provider", "groq", raising=False)
    monkeypatch.setattr(interpret.settings, "classification_model", "llama-3.3-70b-versatile", raising=False)
    monkeypatch.setattr(interpret._client_holder, "get_client", lambda: fake_client)

    await interpret._call_llm("some long enough text for classification that is definitely more than two tokens")

    # Groq quirk: must be small positive float, not 0
    assert captured["temperature"] == 1e-8
    assert captured["model"] == "llama-3.3-70b-versatile"

    # Restore to openrouter for other tests
    monkeypatch.setattr(interpret.settings, "llm_provider", "openrouter", raising=False)
