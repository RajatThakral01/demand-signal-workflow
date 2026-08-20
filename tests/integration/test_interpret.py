"""Interpretation service tests (FR-4, Flow 4 failure entry).

Two groups:
  * Non-network (safe, default): short-text LLM-skip is provably a non-call via a
    spy on ``_call_llm``; provider-failure shows bounded retries + a surfaced
    ``InterpretError`` (never a silent unknown).
  * One real LIVE call: marked ``live`` and skipped unless the tester sets
    ``RUN_LIVE_INTERPRET_TEST=1`` with a real ``OPENROUTER_API_KEY``. This costs a
    tiny amount of real money (approved by Krishnam via Rajat).
"""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import Event, Interpretation
from app.services import interpret
from app.services.ingest import compute_dedupe_key, compute_payload_hash

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


async def _insert_event(db, text: str | None, source="web_form") -> Event:
    from app.db.models import Event as E
    import uuid

    raw = {"message": text} if text else {}
    ev = E(
        id=uuid.uuid4(),
        external_event_id=f"int-{uuid.uuid4()}",
        source=source,
        dedupe_key=compute_dedupe_key(source, f"int-{uuid.uuid4()}"),
        payload_hash=compute_payload_hash({"t": text}),
        is_valid=True,
        schema_version="1.0",
        identity_fields={"name": "Test"} if text else {},
        raw_payload=raw,
        consent=True,
        received_at=NOW,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev


# --- Short text: provably does NOT call the LLM -------------------------------
async def test_short_text_never_calls_llm(db_session, monkeypatch):
    event = await _insert_event(db_session, "hi")

    calls: list = []
    async def _spy(*args, **kwargs):
        calls.append(args)
        raise AssertionError("LLM must not be called for short text")
    monkeypatch.setattr(interpret, "_call_llm", _spy)

    result = await interpret.classify_event(db_session, event)

    assert calls == [], "the LLM was called for sub-min-length text"
    assert result["label"] == "unknown"
    assert result["was_skipped"] is True
    row = (
        await db_session.execute(select(Interpretation).where(
            Interpretation.event_id == event.id))
    ).scalars().first()
    assert row.was_skipped is True
    assert "insufficient_text" in row.reason


# --- Provider failure: bounded retry + visible InterpretError -----------------
async def test_provider_failure_surfaces_error_and_retries_bounded(db_session, monkeypatch):
    event = await _insert_event(
        db_session,
        "our team of ten engineers is evaluating your platform for onboarding and "
        "integration after the pilot and we have several questions about compliance",
    )

    attempts: list = []
    async def _always_fail(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("simulated provider 503")
    monkeypatch.setattr(interpret, "_call_llm", _always_fail)

    # Force a small retry budget so the test is fast and clearly bounded.
    monkeypatch.setattr(interpret.settings, "retry_max_attempts", 3)
    monkeypatch.setattr(interpret.settings, "retry_base_delay_ms", 1)

    with pytest.raises(interpret.InterpretError):
        await interpret.classify_event(db_session, event)

    # Bounded: exactly retry_max_attempts network attempts, not infinite.
    assert len(attempts) == 3
    # No fabricated "unknown" row was written (a failure is NOT a silent unknown).
    row = (
        await db_session.execute(select(Interpretation).where(
            Interpretation.event_id == event.id))
    ).scalars().first()
    assert row is None or row.was_skipped is not True


# --- Real LIVE call (skipped unless explicitly enabled) -----------------------
@pytest.mark.live
async def test_real_live_openrouter_call(db_session):
    if not os.environ.get("RUN_LIVE_INTERPRET_TEST") == "1":
        pytest.skip("set RUN_LIVE_INTERPRET_TEST=1 and OPENROUTER_API_KEY to run the live test")
    event = await _insert_event(
        db_session,
        "We are looking at your pricing tiers and want to know annual commitment discounts",
    )
    result = await interpret.classify_event(db_session, event)
    assert result["status"] == "interpreted"
    assert result["was_skipped"] is False
    for key in ("label", "confidence", "reason", "model_version", "prompt_version"):
        assert key in result, key

    row = (
        await db_session.execute(select(Interpretation).where(
            Interpretation.event_id == event.id))
    ).scalars().first()
    assert row.label  # a real classification, not a fabricated unknown
    assert row.label != "unknown"
    assert float(row.confidence) > 0
    assert row.model_version  # provider/model version recorded
    assert row.prompt_version == interpret.PROMPT_VERSION
    assert row.was_skipped is False
    # Record the actual token usage for cost tracking (surfaced in test output).
    print(f"LIVE label={row.label} confidence={row.confidence} "
          f"tokens={row.token_usage} model={row.model_version}")