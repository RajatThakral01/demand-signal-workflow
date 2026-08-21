"""Integration tests — admin replay + simulate-failure (Phase 8c, FR-11).

Covers bearer-token gating (401), replay of dead-lettered events (happy path via
simulate-failure), 404/409 error states, and the critical replay-after-partial-
success idempotency test (no duplicate interpretations/scores/leads/routes,
DLQ row resolved) with printed row counts.
"""

import uuid
from datetime import datetime, timezone

import openai
from sqlalchemy import func, select

from app.db.models import (
    DeadLetterQueue,
    IdentityLink,
    Interpretation,
    Lead,
    Receipt,
    Route,
    Score,
)
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

TOKEN = "test_admin_key"  # matches conftest ADMIN_API_KEY default


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": f"adm-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "consent": True,
        "email": f"adm-{uuid.uuid4()}@example.com",
        "message": (
            "our team of ten engineers is evaluating your platform for onboarding "
            "and integration after the pilot and we have several questions about "
            "compliance and security requirements"
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


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalars().one()


async def _dead_letter_via_simulate(client, event_id: str) -> dict:
    return await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "interpret", "event_id": event_id},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


# --- 401 gating ---------------------------------------------------------------
async def test_replay_401_without_token(client, db_session, monkeypatch):
    resp = await client.post("/api/v1/admin/replay/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "unauthorized"


async def test_replay_401_wrong_token(client, db_session, monkeypatch):
    resp = await client.post(
        "/api/v1/admin/replay/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "unauthorized"


async def test_simulate_failure_401_without_token(client, db_session, monkeypatch):
    resp = await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "interpret", "event_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


async def test_simulate_failure_401_wrong_token(client, db_session, monkeypatch):
    resp = await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "interpret", "event_id": str(uuid.uuid4())},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


# --- Happy paths --------------------------------------------------------------
async def test_replay_happy_path(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    event_id = posted["event_id"]

    sim = await _dead_letter_via_simulate(client, event_id)
    assert sim.status_code == 200
    # correct token now permits replay
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    replay = await client.post(
        f"/api/v1/admin/replay/{event_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert replay.status_code == 200
    body = replay.json()
    assert body["status"] == "replayed"
    assert body["event_id"] == event_id
    assert body["lead_id"]

    oneres = (
        await db_session.execute(select(Receipt).where(
            Receipt.action_type == "dead_letter_resolved",
            Receipt.event_id == uuid.UUID(event_id),
        ))
    ).scalars().all()
    assert len(oneres) == 1


async def test_simulate_failure_happy_path(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    sim = await _dead_letter_via_simulate(client, posted["event_id"])
    assert sim.status_code == 200
    body = sim.json()
    assert body["status"] == "dead_lettered"
    assert body["stage"] == "interpret"
    dlq = (
        await db_session.execute(select(DeadLetterQueue).where(
            DeadLetterQueue.event_id == uuid.UUID(posted["event_id"])
        ))
    ).scalars().all()
    assert len(dlq) == 1
    assert dlq[0].resolved is False


# --- Error states -------------------------------------------------------------
async def test_replay_404_nonexistent_event(client, db_session, monkeypatch):
    resp = await client.post(
        "/api/v1/admin/replay/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 404


async def test_replay_not_dead_lettered_409(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    resp = await client.post(
        f"/api/v1/admin/replay/{posted['event_id']}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "not_dead_lettered"


async def test_replay_already_resolved_409_after_successful_replay(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    event_id = posted["event_id"]
    await _dead_letter_via_simulate(client, event_id)
    ok = await client.post(
        f"/api/v1/admin/replay/{event_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert ok.status_code == 200
    again = await client.post(
        f"/api/v1/admin/replay/{event_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "not_dead_lettered"


async def test_simulate_failure_invalid_stage_400(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    resp = await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "score", "event_id": posted["event_id"]},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_stage"


async def test_simulate_failure_404(client, db_session, monkeypatch):
    resp = await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "interpret", "event_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 404


# --- THE CRITICAL TEST: replay after partial success, idempotent writes ---------
async def test_replay_partial_success_is_idempotent(client, db_session, monkeypatch):
    """Dead-letter via simulate-failure, then replay, and assert exactly ONE row
    each for interpretations/scores/leads/routes (no duplicates from the replay)."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    event_id = posted["event_id"]

    print(f"\n[replay-idempotency] event_id={event_id}")
    print(f"[replay-idempotency] BEFORE dead-letter: "
          f"interpretations={await _count(db_session, Interpretation)} "
          f"scores={await _count(db_session, Score)} "
          f"leads={await _count(db_session, Lead)} "
          f"routes={await _count(db_session, Route)} "
          f"dlq={await _count(db_session, DeadLetterQueue)} "
          f"identity_links={await _count(db_session, IdentityLink)}")

    sim = await _dead_letter_via_simulate(client, event_id)
    assert sim.status_code == 200

    # The event was dead-lettered BEFORE interpret ran (simulate-failure dead-letters
    # directly), so no interpretation/score/lead/route exists yet for it.
    print(f"[replay-idempotency] AFTER dead-letter (pre-replay): "
          f"interpretations={await _count(db_session, Interpretation)} "
          f"scores={await _count(db_session, Score)} "
          f"leads={await _count(db_session, Lead)} "
          f"routes={await _count(db_session, Route)} "
          f"dlq={await _count(db_session, DeadLetterQueue)}")

    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    replay = await client.post(
        f"/api/v1/admin/replay/{event_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert replay.status_code == 200, replay.text

    # The critical assertions with actual counts printed.
    n_interp = await _count(db_session, Interpretation)
    n_scores = await _count(db_session, Score)
    n_leads = await _count(db_session, Lead)
    n_routes = await _count(db_session, Route)
    n_dlq = await _count(db_session, DeadLetterQueue)
    n_links = await _count(db_session, IdentityLink)
    print(f"[replay-idempotency] AFTER replay: "
          f"interpretations={n_interp} scores={n_scores} "
          f"leads={n_leads} routes={n_routes} dlq={n_dlq} identity_links={n_links}")

    # Exactly one row for that event across each entity (the replay must NOT have
    # double-written anything — same as the routes-idempotency fix in Phase 8a).
    assert n_interp == 1, f"expected 1 interpretation, got {n_interp}"
    assert n_scores == 1, f"expected 1 score, got {n_scores}"
    assert n_leads == 1, f"expected 1 lead, got {n_leads}"
    assert n_routes == 1, f"expected 1 route, got {n_routes}"
    assert n_links == 1, f"expected 1 identity link, got {n_links}"

    # The DLQ row for this event is now resolved.
    dlq_rows = (
        await db_session.execute(select(DeadLetterQueue).where(
            DeadLetterQueue.event_id == uuid.UUID(event_id)
        ))
    ).scalars().all()
    assert len(dlq_rows) == 1
    assert dlq_rows[0].resolved is True
    print(f"[replay-idempotency] dlq resolved={dlq_rows[0].resolved} "
          f"stage={dlq_rows[0].stage} retry_count={dlq_rows[0].retry_count}")


# --- Replay when interpret STILL failing -> re-dead-letter (503) -----------------
async def test_replay_redeadletters_when_provider_still_down(client, db_session, monkeypatch):
    """If the replay attempt itself fails, classify_event dead-letters again (a NEW
    DLQ row + dead_lettered receipt), and the endpoint returns a failure, not 200."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    event_id = posted["event_id"]

    # Dead-letter via simulate-failure (token-gated)
    await _dead_letter_via_simulate(client, event_id)

    # Now force interpret to keep failing during replay.
    async def _always_fail(*args, **kwargs):
        raise openai.APITimeoutError(request=object())

    monkeypatch.setattr(interpret, "_call_llm", _always_fail)
    monkeypatch.setattr(interpret.settings, "retry_max_attempts", 1)
    monkeypatch.setattr(interpret.settings, "retry_base_delay_ms", 1)

    replay = await client.post(
        f"/api/v1/admin/replay/{event_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert replay.status_code == 503
    assert replay.json()["detail"]["error"] == "replay_failed"

    # classify_event's exhaustion path wrote a NEW DLQ row (the original simulate
    # row was still unresolved) + a dead_lettered receipt.
    dlq_rows = (
        await db_session.execute(select(DeadLetterQueue).where(
            DeadLetterQueue.event_id == uuid.UUID(event_id)
        ))
    ).scalars().all()
    assert len(dlq_rows) == 2, f"expected 2 DLQ rows (simulate + re-dead-letter), got {len(dlq_rows)}"
    assert all(r.resolved is False for r in dlq_rows)
    dl_lettered = (
        await db_session.execute(select(Receipt).where(
            Receipt.action_type == "dead_lettered",
            Receipt.event_id == uuid.UUID(event_id),
        ))
    ).scalars().all()
    assert len(dl_lettered) == 2, f"expected 2 dead_lettered receipts, got {len(dl_lettered)}"