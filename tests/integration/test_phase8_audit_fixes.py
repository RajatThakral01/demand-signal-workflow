"""Integration tests — Phase 8 audit fixes (regression suite).

Every test here fails against the pre-fix code. Grouped by the defect it pins:

  1. ``payload_hash`` not advanced on edit (app/services/ingest.py) — an edited
     event stayed permanently unequal to its own content, so the same payload
     re-detected as an edit forever and the original payload read as a duplicate.
  2. ``create_new`` manual-review resolution minted an identity with no
     ``identity_created`` receipt — a silent FR-9 violation that put the
     identities/identity_created reconciliation pair at nonzero variance.
  3. Resolving a manual review never resumed the pipeline — the event was linked
     to an identity and then sat there forever with no interpretation, score,
     lead, route or attribution touch (Flow 3 step 4 unimplemented).
  4. ``events.dedupe_key`` carried a *global* UNIQUE constraint, so resubmitting
     the same schema-invalid payload raised IntegrityError -> 500, and a corrected
     resubmission was misread as an edit of the still-``is_valid=false`` row.
  5. ``routes.escalated`` was never evaluated; the SLA breach was documented as
     computed-on-read but nothing computed it.
  6. ``manual_review_queue.resolved_at`` was left NULL on resolution.
  7. (found while fixing) ``events_edited`` reconciliation counted raw receipts
     against a sticky boolean, so a twice-edited event reported variance 1.

Plus the two API gaps: ``GET /api/v1/dead-letter`` (promised by the PRD Error
States table, never built) and the reconciliation response's top-level
``{"variance", "status"}`` / ``since``/``until`` window.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.db.models import (
    AttributionTouch,
    Event,
    Identity,
    Interpretation,
    Lead,
    ManualReviewQueue,
    Receipt,
    Route,
    Score,
)
from app.services import interpret

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

TOKEN = "test_admin_key"  # matches conftest ADMIN_API_KEY default

# Long enough to clear settings.interpret_min_tokens (8 words) so the stubbed LLM
# path runs instead of the deterministic `unknown` short-circuit.
LONG_TEXT = (
    "our team of ten engineers is evaluating your platform for onboarding and "
    "integration after the pilot and we have questions about pricing tiers"
)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": f"p8-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "consent": True,
        "email": f"p8-{uuid.uuid4()}@example.com",
        "message": LONG_TEXT,
    }
    payload.update(overrides)
    return payload


def _social(**overrides):
    payload = {
        "source": "social_mention",
        "external_event_id": f"p8-sm-{uuid.uuid4()}",
        "received_at": NOW.isoformat(),
        "text": LONG_TEXT,
        "company": "Example",
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


async def _count(db, model, *where):
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return (await db.execute(stmt)).scalars().one()


async def _receipts(db, action_type, event_id=None):
    stmt = select(Receipt).where(Receipt.action_type == action_type)
    if event_id is not None:
        stmt = stmt.where(Receipt.event_id == uuid.UUID(str(event_id)))
    return (await db.execute(stmt)).scalars().all()


async def _variance_is_zero(client):
    """Assert the reconciliation gate passes, returning the body for messages."""
    body = (await client.get("/api/v1/dashboard/reconciliation")).json()
    mismatched = [r for r in body["reconciliation"] if r["variance"] != 0]
    assert body["variance"] == 0, f"nonzero variance: {mismatched}"
    assert body["status"] == "PASS"
    return body


# =============================================================================
# Defect 1 — payload_hash must advance on edit
# =============================================================================
async def test_resubmitting_the_edited_payload_is_a_duplicate_not_another_edit(
    client, db_session, monkeypatch
):
    """Pre-fix: the row kept the ORIGINAL hash, so the edited payload compared
    unequal on every resubmission and was re-detected as an edit without bound."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    original = _web_form()
    first = await client.post("/api/v1/events", json=original)
    assert first.status_code in (200, 202), first.text

    edited = {**original, "message": LONG_TEXT + " and also about SOC2 compliance"}
    edit = await client.post("/api/v1/events", json=edited)
    assert edit.status_code in (200, 202)
    assert edit.json()["is_edit"] is True

    # THE regression: submitting the same edited payload again is a true duplicate.
    again = await client.post("/api/v1/events", json=edited)
    assert again.status_code == 200
    assert again.json()["duplicate"] is True, "edited payload re-detected as an edit"

    # Exactly one event row and exactly one event_edited receipt.
    assert (await _count(db_session, Event)) == 1
    assert len(await _receipts(db_session, "event_edited")) == 1


async def test_payload_hash_is_advanced_to_the_incoming_hash(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    original = _web_form()
    await client.post("/api/v1/events", json=original)
    row = (await db_session.execute(select(Event))).scalars().one()
    hash_before = row.payload_hash

    edited = {**original, "message": LONG_TEXT + " plus a follow-up question"}
    await client.post("/api/v1/events", json=edited)

    db_session.expire_all()
    row = (await db_session.execute(select(Event))).scalars().one()
    assert row.payload_hash != hash_before, "payload_hash left stale after edit"

    # The receipt records both hashes, so the edit is auditable.
    receipt = (await _receipts(db_session, "event_edited"))[0]
    assert receipt.meta["previous_payload_hash"] == hash_before
    assert receipt.meta["payload_hash"] == row.payload_hash


async def test_resubmitting_the_original_after_an_edit_is_an_edit_again(
    client, db_session, monkeypatch
):
    """Pre-fix the original payload read as a *duplicate* (it still matched the
    stale stored hash), silently discarding a real revert."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    original = _web_form()
    await client.post("/api/v1/events", json=original)
    await client.post("/api/v1/events", json={**original, "message": LONG_TEXT + " v2"})

    revert = await client.post("/api/v1/events", json=original)
    assert revert.status_code in (200, 202)
    assert revert.json()["is_edit"] is True, "revert to the original read as a duplicate"


async def test_two_distinct_edits_keep_reconciliation_variance_zero(
    client, db_session, monkeypatch
):
    """Defect 7: `events.is_edit` is sticky, so the entity side counts 1 while two
    legitimate `event_edited` receipts exist. Only COUNT(DISTINCT event_id) pairs
    exactly — pre-fix this reported variance 1 and failed the FR-10 gate."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    original = _web_form()
    await client.post("/api/v1/events", json=original)
    await client.post("/api/v1/events", json={**original, "message": LONG_TEXT + " v2"})
    await client.post("/api/v1/events", json={**original, "message": LONG_TEXT + " v3"})

    assert len(await _receipts(db_session, "event_edited")) == 2
    assert (await _count(db_session, Event, Event.is_edit.is_(True))) == 1
    await _variance_is_zero(client)


# =============================================================================
# Defect 2 — create_new resolution must write an identity_created receipt
# =============================================================================
async def test_create_new_resolution_writes_identity_created_receipt(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    await client.post("/api/v1/events", json=_social(display_name="Ada Lovelace"))
    parked = await client.post("/api/v1/events", json=_social(display_name="Ada Rutherford"))
    assert parked.json()["status"] == "manual_review"
    review_id = parked.json()["review_id"]

    identities_before = await _count(db_session, Identity)
    resolved = await client.post(
        f"/api/v1/manual-review/{review_id}/resolve", json={"decision": "create_new"}
    )
    assert resolved.status_code in (200, 202), resolved.text

    db_session.expire_all()
    assert (await _count(db_session, Identity)) == identities_before + 1
    created = await _receipts(db_session, "identity_created")
    assert len(created) == identities_before + 1, (
        "create_new minted an identity with no identity_created receipt"
    )
    # Every identity row is receipted -> the reconciliation pair holds.
    await _variance_is_zero(client)


async def test_resolution_sets_resolved_at(client, db_session, monkeypatch):
    """Defect 6: resolved_at stayed NULL, so the queue had no audit of *when*."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    await client.post("/api/v1/events", json=_social(display_name="Ada Lovelace"))
    parked = await client.post("/api/v1/events", json=_social(display_name="Ada Rutherford"))
    review_id = parked.json()["review_id"]

    await client.post(
        f"/api/v1/manual-review/{review_id}/resolve", json={"decision": "create_new"}
    )
    db_session.expire_all()
    entry = (
        await db_session.execute(
            select(ManualReviewQueue).where(ManualReviewQueue.id == uuid.UUID(review_id))
        )
    ).scalars().one()
    assert entry.status == "resolved"
    assert entry.resolved_at is not None, "resolved_at left NULL"

    # And the list endpoint surfaces it.
    listing = (await client.get("/api/v1/manual-review?status=resolved")).json()
    match = [e for e in listing if e["id"] == review_id]
    assert match and match[0]["resolved_at"] is not None


# =============================================================================
# Defect 3 — resolving a review must resume the pipeline (Flow 3 step 4)
# =============================================================================
async def test_resolve_create_new_resumes_the_pipeline(client, db_session, monkeypatch):
    """Pre-fix: the response carried only the review + identity, and no
    interpretation / score / lead / route / touch was ever written."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    await client.post("/api/v1/events", json=_social(display_name="Ada Lovelace"))
    parked = await client.post("/api/v1/events", json=_social(display_name="Ada Rutherford"))
    review_id = parked.json()["review_id"]
    parked_event_id = parked.json()["event_id"]

    resolved = await client.post(
        f"/api/v1/manual-review/{review_id}/resolve", json={"decision": "create_new"}
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["event_id"] == parked_event_id
    assert body["pipeline_status"] == "resumed", "pipeline did not resume on resolution"
    assert body["label"] == "pricing_inquiry"
    assert body["score"] is not None
    assert body["decision_outcome"] is not None
    assert body["lead_id"], "no lead created for the resumed event"
    assert body["queue"]
    assert body["sla_deadline"]

    # The rows actually exist for the previously-parked event.
    ev_uuid = uuid.UUID(parked_event_id)
    assert (await _count(db_session, Interpretation,
                         Interpretation.event_id == ev_uuid)) == 1
    assert (await _count(db_session, Score, Score.event_id == ev_uuid)) == 1
    assert (await _count(db_session, Lead, Lead.source_event_id == ev_uuid)) == 1
    lead = (
        await db_session.execute(select(Lead).where(Lead.source_event_id == ev_uuid))
    ).scalars().one()
    assert (await _count(db_session, Route, Route.lead_id == lead.id)) == 1
    assert (await _count(db_session, AttributionTouch,
                         AttributionTouch.identity_id == lead.identity_id)) == 1
    await _variance_is_zero(client)


async def test_resolve_merge_into_resumes_and_updates_the_existing_lead(
    client, db_session, monkeypatch
):
    """merge_into attaches the event to an identity that already has a lead, so
    the resumed act() stage must UPDATE rather than create a second lead."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    seed = await client.post("/api/v1/events", json=_social(display_name="Ada Lovelace"))
    target_identity = seed.json()["identity_id"]
    assert seed.json()["lead_id"], "seed event should have produced a lead to update"

    parked = await client.post("/api/v1/events", json=_social(display_name="Ada Rutherford"))
    review_id = parked.json()["review_id"]

    resolved = await client.post(
        f"/api/v1/manual-review/{review_id}/resolve",
        json={"decision": "merge_into", "identity_id": target_identity},
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["identity_id"] == target_identity
    assert body["pipeline_status"] == "resumed"
    assert body["lead_op"] == "updated", f"expected lead update, got {body['lead_op']}"

    # One lead for the merged identity, not two.
    assert (await _count(db_session, Lead,
                         Lead.identity_id == uuid.UUID(target_identity))) == 1
    await _variance_is_zero(client)


# =============================================================================
# Defect 4 — dedupe_key uniqueness must be scoped to accepted rows
# =============================================================================
async def test_same_invalid_payload_twice_returns_200_twice(client, db_session):
    """Pre-fix the second submission hit the global UNIQUE(dedupe_key) and raised
    IntegrityError -> 500, which also violated "never silently drop an invalid
    event" (the caller got an error, not a persisted rejection)."""
    bad = _web_form(email="not-an-email", external_event_id="p8-invalid-repeat")

    first = await client.post("/api/v1/events", json=bad)
    assert first.status_code == 200
    assert first.json()["is_valid"] is False

    second = await client.post("/api/v1/events", json=bad)
    assert second.status_code == 200, f"repeat rejection returned {second.status_code}"
    assert second.json()["is_valid"] is False
    assert second.json()["event_id"] != first.json()["event_id"]

    # Two rejected rows AND two event_rejected receipts — the pair stays exact.
    assert (await _count(db_session, Event, Event.is_valid.is_(False))) == 2
    assert len(await _receipts(db_session, "event_rejected")) == 2


async def test_repeat_rejections_keep_reconciliation_variance_zero(client, db_session):
    bad = _web_form(email="still-not-an-email", external_event_id="p8-invalid-x3")
    for _ in range(3):
        assert (await client.post("/api/v1/events", json=bad)).status_code == 200
    assert (await _count(db_session, Event, Event.is_valid.is_(False))) == 3
    await _variance_is_zero(client)


async def test_corrected_resubmission_creates_a_new_event_not_an_edit(
    client, db_session, monkeypatch
):
    """Pre-fix the corrected payload matched the rejected row's dedupe_key and was
    treated as an EDIT of it — running the full pipeline against a row still
    flagged is_valid=false, and never producing a clean accepted event."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    ext_id = "p8-corrected"
    bad = _web_form(email="broken", external_event_id=ext_id)
    rejected = await client.post("/api/v1/events", json=bad)
    assert rejected.json()["is_valid"] is False
    rejected_id = rejected.json()["event_id"]

    fixed = await client.post(
        "/api/v1/events",
        json=_web_form(email="fixed@example.com", external_event_id=ext_id),
    )
    assert fixed.status_code in (200, 202), fixed.text
    body = fixed.json()
    assert body["is_valid"] is True
    assert body["is_edit"] is False, "corrected resubmission misread as an edit"
    assert body["event_id"] != rejected_id, "pipeline ran against the rejected row"

    # Both rows survive: the rejected one as an audit breadcrumb, the accepted one
    # as the live event. Same dedupe_key, permitted by the partial index.
    db_session.expire_all()
    rows = (await db_session.execute(select(Event))).scalars().all()
    assert len(rows) == 2
    keys = {r.dedupe_key for r in rows}
    assert len(keys) == 1, "the two rows should share one dedupe_key"
    assert sorted(r.is_valid for r in rows) == [False, True]
    assert len(await _receipts(db_session, "event_edited")) == 0
    await _variance_is_zero(client)


# =============================================================================
# Defect 5 — SLA escalation must be evaluated, persisted and receipted on read
# =============================================================================
async def _lead_with_backdated_sla(client, db_session, monkeypatch):
    """Create a routed lead, then backdate its SLA deadline into the past."""
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = await client.post("/api/v1/events", json=_web_form())
    lead_id = posted.json()["lead_id"]
    assert lead_id, posted.text

    await db_session.execute(
        update(Route)
        .where(Route.lead_id == uuid.UUID(lead_id))
        .values(sla_deadline=datetime.now(timezone.utc) - timedelta(hours=1))
    )
    await db_session.commit()
    db_session.expire_all()
    return lead_id


async def test_breached_sla_is_escalated_on_read(client, db_session, monkeypatch):
    """Pre-fix `escalated` was hardcoded to the stored column, which nothing ever
    set — a breached SLA stayed invisible forever."""
    lead_id = await _lead_with_backdated_sla(client, db_session, monkeypatch)

    detail = await client.get(f"/api/v1/leads/{lead_id}")
    assert detail.status_code == 200
    assert detail.json()["escalated"] is True, "breached SLA not escalated on read"

    # Persisted, not just computed for the response.
    db_session.expire_all()
    route = (
        await db_session.execute(select(Route).where(Route.lead_id == uuid.UUID(lead_id)))
    ).scalars().one()
    assert route.escalated is True

    # And receipted exactly once (FR-9 names `escalated`).
    assert len(await _receipts(db_session, "escalated")) == 1


async def test_escalation_is_idempotent_across_repeated_reads(
    client, db_session, monkeypatch
):
    """The read path is allowed one write; it must not write again on every GET."""
    lead_id = await _lead_with_backdated_sla(client, db_session, monkeypatch)

    for _ in range(3):
        assert (await client.get(f"/api/v1/leads/{lead_id}")).json()["escalated"] is True
    await client.get("/api/v1/leads")  # the list endpoint evaluates too

    db_session.expire_all()
    receipts = await _receipts(db_session, "escalated")
    assert len(receipts) == 1, f"expected 1 escalated receipt, got {len(receipts)}"
    await _variance_is_zero(client)


async def test_unbreached_sla_is_not_escalated(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = await client.post("/api/v1/events", json=_web_form())
    lead_id = posted.json()["lead_id"]
    assert lead_id, posted.text

    detail = await client.get(f"/api/v1/leads/{lead_id}")
    assert detail.json()["escalated"] is False
    assert len(await _receipts(db_session, "escalated")) == 0


async def test_list_leads_exposes_escalated(client, db_session, monkeypatch):
    """`escalated` was absent from the list payload entirely (only detail had it)."""
    lead_id = await _lead_with_backdated_sla(client, db_session, monkeypatch)
    listing = (await client.get("/api/v1/leads")).json()
    match = [x for x in listing if x["lead_id"] == lead_id]
    assert match, "lead missing from listing"
    assert "escalated" in match[0]
    assert match[0]["escalated"] is True


# =============================================================================
# GET /api/v1/dead-letter — the endpoint the PRD promised
# =============================================================================
async def test_dead_letter_listing_shows_outstanding_entries(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    event_id = posted["event_id"]
    sim = await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "interpret", "event_id": event_id},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert sim.status_code == 200

    listing = await client.get("/api/v1/dead-letter")
    assert listing.status_code == 200
    entries = listing.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event_id"] == event_id
    assert entry["stage"] == "interpret"
    assert entry["error"]
    assert entry["retry_count"] >= 1
    assert entry["resolved"] is False
    assert entry["replay_url"] == f"/api/v1/admin/replay/{event_id}"


async def test_dead_letter_listing_defaults_to_unresolved_and_filters(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    posted = (await client.post("/api/v1/events", json=_web_form())).json()
    event_id = posted["event_id"]
    await client.post(
        "/api/v1/admin/simulate-failure",
        json={"stage": "interpret", "event_id": event_id},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    replay = await client.post(
        f"/api/v1/admin/replay/{event_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert replay.status_code == 200, replay.text

    # Default view (?resolved=false) is now empty; the resolved view has it.
    assert (await client.get("/api/v1/dead-letter")).json() == []
    resolved = (await client.get("/api/v1/dead-letter?resolved=true")).json()
    assert len(resolved) == 1
    assert resolved[0]["event_id"] == event_id
    assert resolved[0]["resolved"] is True


async def test_dead_letter_listing_is_empty_when_nothing_failed(client, db_session):
    resp = await client.get("/api/v1/dead-letter")
    assert resp.status_code == 200
    assert resp.json() == []


# =============================================================================
# Reconciliation response shape — top-level variance/status + window
# =============================================================================
async def test_reconciliation_exposes_top_level_variance_and_status(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    await client.post("/api/v1/events", json=_web_form())
    body = (await client.get("/api/v1/dashboard/reconciliation")).json()
    assert body["variance"] == 0
    assert body["status"] == "PASS"
    # Backward-compatible aliases retained for existing callers.
    assert body["total_variance"] == 0
    assert body["overall_status"] == "ok"
    assert isinstance(body["reconciliation"], list)
    for row in body["reconciliation"]:
        assert {"entity", "dashboard_count", "receipt_count", "variance",
                "status"} <= set(row)


async def test_reconciliation_accepts_a_time_window(client, db_session, monkeypatch):
    monkeypatch.setattr(interpret, "_call_llm", _fake_call_llm())
    await client.post("/api/v1/events", json=_web_form())
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    windowed = await client.get(
        "/api/v1/dashboard/reconciliation", params={"since": since, "until": until}
    )
    assert windowed.status_code == 200
    body = windowed.json()
    assert body["window"]["since"] is not None
    assert body["window"]["until"] is not None
    assert body["variance"] == 0

    # A window entirely in the past sees nothing at all — still variance 0.
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    older = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
    empty = (
        await client.get(
            "/api/v1/dashboard/reconciliation", params={"since": old, "until": older}
        )
    ).json()
    assert empty["variance"] == 0
    assert all(r["dashboard_count"] == 0 for r in empty["reconciliation"])
