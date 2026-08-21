"""Integration tests — dead-letter path (FR-11, Phase 8b).

When the ONE external provider call (interpret) exhausts its bounded retries, the
event is dead-lettered: a dead_letter_queue row + `dead_lettered` receipt are
written atomically, the pipeline halts (no score/lead/route), no fabricated
`unknown` interpretation is produced, and the API responds with
status="dead_letter" / stage="interpret".
"""

import uuid
from datetime import datetime, timezone

import openai
import pytest
from sqlalchemy import func, select

from app.db.models import (
    DeadLetterQueue,
    Interpretation,
    Lead,
    Receipt,
    Route,
    Score,
)
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": f"dlq-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "consent": True,
        "email": f"dlq-{uuid.uuid4()}@example.com",
        "message": (
            "our team of ten engineers is evaluating your platform for onboarding "
            "and integration after the pilot and we have several questions about "
            "compliance and security requirements"
        ),
    }
    payload.update(overrides)
    return payload


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalars().one()


async def test_exhaustion_dead_letters_event_and_halts_pipeline(client, db_session, monkeypatch):
    """All configured retries fail -> dead_lettered, pipeline halts, no fabricated
    unknown, no score/lead/route, and the API reflects the dead-letter state."""
    attempts: list = []
    async def _always_fail(*args, **kwargs):
        attempts.append(1)
        raise openai.APITimeoutError(request=object())

    monkeypatch.setattr(interpret, "_call_llm", _always_fail)
    monkeypatch.setattr(interpret.settings, "retry_max_attempts", 3)
    monkeypatch.setattr(interpret.settings, "retry_base_delay_ms", 1)

    resp = await client.post("/api/v1/events", json=_web_form())
    assert resp.status_code == 202
    body = resp.json()

    # API response reflects the dead-letter state.
    assert body["status"] == "dead_letter"
    assert body["stage"] == "interpret"
    assert body["is_valid"] is True

    # Exactly RETRY_MAX_ATTEMPTS network attempts (spy-count, not "eventually failed").
    assert len(attempts) == 3

    event_id = body["event_id"]
    dlq = (
        await db_session.execute(select(DeadLetterQueue).where(
            DeadLetterQueue.event_id == uuid.UUID(event_id)
        ))
    ).scalars().all()
    assert len(dlq) == 1
    assert dlq[0].stage == "interpret"
    assert dlq[0].retry_count == 3
    assert dlq[0].resolved is False
    assert dlq[0].error  # sanitized error message present

    # A `dead_lettered` receipt was written for this event...
    dl_receipts = (
        await db_session.execute(select(Receipt).where(
            Receipt.action_type == "dead_lettered",
            Receipt.event_id == uuid.UUID(event_id),
        ))
    ).scalars().all()
    assert len(dl_receipts) == 1
    # ...and NO `error`-status interpreted receipt for this event.
    error_receipts = (
        await db_session.execute(select(Receipt).where(
            Receipt.action_type == "interpreted",
            Receipt.event_id == uuid.UUID(event_id),
            Receipt.status == "error",
        ))
    ).scalars().all()
    assert len(error_receipts) == 0

    # Pipeline halted: no fabricated unknown interpretation, no score/lead/route.
    interp = (
        await db_session.execute(select(Interpretation).where(
            Interpretation.event_id == uuid.UUID(event_id)
        ))
    ).scalars().all()
    assert all(i.label != "unknown" for i in interp)
    assert (await _count(db_session, Score)) == 0
    assert (await _count(db_session, Lead)) == 0
    assert (await _count(db_session, Route)) == 0


async def test_dead_letter_respects_retry_budget(db_session, monkeypatch):
    """With retry_max_attempts=1, exactly one attempt is made before dead-lettering."""
    attempts: list = []
    async def _always_fail(*args, **kwargs):
        attempts.append(1)
        raise openai.APITimeoutError(request=object())

    monkeypatch.setattr(interpret, "_call_llm", _always_fail)
    monkeypatch.setattr(interpret.settings, "retry_max_attempts", 1)
    monkeypatch.setattr(interpret.settings, "retry_base_delay_ms", 1)

    from app.services.ingest import compute_dedupe_key, compute_payload_hash
    from app.db.models import Event
    ev = Event(
        id=uuid.uuid4(),
        external_event_id="dlq-budget-1",
        source="web_form",
        dedupe_key=compute_dedupe_key("web_form", "dlq-budget-1"),
        payload_hash=compute_payload_hash({"t": "x"}),
        is_valid=True,
        schema_version="1.0",
        identity_fields={"email": "dlq-b@example.com"},
        raw_payload={"message": (
            "evaluating the platform with several compliance questions for our ten "
            "engineers before we decide on the integration approach and rollout")},
        consent=True,
        received_at=NOW,
    )
    db_session.add(ev)
    await db_session.commit()

    with pytest.raises(interpret.InterpretError):
        await interpret.classify_event(db_session, ev)
    assert len(attempts) == 1

    dlq = (
        await db_session.execute(select(DeadLetterQueue).where(
            DeadLetterQueue.event_id == ev.id
        ))
    ).scalars().all()
    assert len(dlq) == 1
    assert dlq[0].retry_count == 1