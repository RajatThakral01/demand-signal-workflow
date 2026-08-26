"""Proof that Fix 1 is needed: threshold is currently ignored.

This file is the FAIL-before-fix / PASS-after-fix proof for Fix 1.
It will be kept as regression after the fix, not removed.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.models import ManualReviewQueue
from app.services import resolve as resolve_svc

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

async def _insert_event(db, event_id, identity_fields, source="web_form"):
    from app.db.models import Event
    from app.services.ingest import compute_dedupe_key, compute_payload_hash
    ev = Event(
        id=event_id,
        external_event_id=str(event_id),
        source=source,
        dedupe_key=compute_dedupe_key(source, str(event_id)),
        payload_hash=compute_payload_hash({"id": str(event_id)}),
        is_valid=True,
        schema_version="1.0",
        identity_fields=identity_fields,
        raw_payload={"identity": identity_fields},
        consent=True,
        received_at=NOW,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev

async def test_fix1_low_confidence_should_have_no_candidate(db_session, monkeypatch):
    """Seed Ada Lovelace, then send a name that fuzzy-matches with 0.10. Current code proposes the low-confidence candidate; fixed code must return None."""
    from decimal import Decimal
    # Force similarity to 0.10
    monkeypatch.setattr(resolve_svc, "fuzzy_similarity", lambda *a, **kw: Decimal("0.10"))

    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"

    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Rutherford"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    # FIX 1 REQUIRED: below-threshold must have no candidate and that reason
    assert r2["candidate_identity_id"] is None, f"expected None candidate for low score 0.10, got {r2['candidate_identity_id']}"
    entry = (await db_session.execute(select(ManualReviewQueue).where(ManualReviewQueue.event_id == e2.id))).scalars().first()
    assert entry.candidate_identity_id is None
    assert entry.reason == "no_confident_fuzzy_candidate"
    assert resolve_svc.should_auto_link(Decimal("0.10"), Decimal("0.85")) is False


async def test_fix1_boundary_085_has_candidate(db_session, monkeypatch):
    """At exactly 0.85 threshold, candidate must be present (boundary case)."""
    from decimal import Decimal
    monkeypatch.setattr(resolve_svc, "fuzzy_similarity", lambda *a, **kw: Decimal("0.85"))
    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"
    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace Clone"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    assert r2["candidate_identity_id"] == r1["identity_id"], "0.85 boundary must have candidate"
    entry = (await db_session.execute(select(ManualReviewQueue).where(ManualReviewQueue.event_id == e2.id))).scalars().first()
    assert entry.candidate_identity_id == r1["identity_id"]
    assert "fuzzy_name_company_manual_review:0.85" in entry.reason
    assert resolve_svc.should_auto_link(Decimal("0.85"), Decimal("0.85")) is False


async def test_fix1_high_095_has_candidate(db_session, monkeypatch):
    """At 0.95 above threshold, candidate must be present."""
    from decimal import Decimal
    monkeypatch.setattr(resolve_svc, "fuzzy_similarity", lambda *a, **kw: Decimal("0.95"))
    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"
    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace Clone"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    assert r2["candidate_identity_id"] == r1["identity_id"]
    entry = (await db_session.execute(select(ManualReviewQueue).where(ManualReviewQueue.event_id == e2.id))).scalars().first()
    assert entry.candidate_identity_id == r1["identity_id"]
    assert "fuzzy_name_company_manual_review:0.95" in entry.reason
    assert resolve_svc.should_auto_link(Decimal("0.95"), Decimal("0.85")) is False
