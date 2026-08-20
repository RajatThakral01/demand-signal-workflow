"""Integration tests — identity resolution + manual review flow (FR-3, Flow 3).

Runs against a real test Postgres: email/phone auto-link, fuzzy above-threshold
auto-link, fuzzy below-threshold → manual review (pipeline halts), the resolve
endpoint (merge_into / create_new), and the "never force-merge below threshold"
invariant exercised via a real resolution call.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Identity, IdentityLink, ManualReviewQueue
from app.services import resolve as resolve_svc

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _event(**overrides):
    base = {
        "id": None,  # set by caller
        "identity_fields": {},
    }
    return base | overrides


async def _insert_event(db, event_id, identity_fields, source="web_form"):
    from app.db.models import Event
    from app.services.ingest import compute_dedupe_key, compute_payload_hash

    ev = Event(
        id=event_id,
        external_event_id=str(event_id),  # unique dedupe key per event
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


# --- Exact email auto-link ----------------------------------------------------
async def test_exact_email_auto_links(db_session):
    import uuid

    ev = await _insert_event(db_session, uuid.uuid4(), {"email": "ada@example.com", "name": "Ada"})
    result = await resolve_svc.resolve_identity(db_session, ev)
    assert result["status"] == "linked"
    assert result["rule"] == "exact_email"
    assert result["confidence"] == 1.00

    links = (await db_session.execute(select(IdentityLink))).scalars().all()
    assert len(links) == 1
    assert links[0].match_rule == "exact_email"


async def test_exact_phone_auto_links(db_session):
    import uuid

    ev = await _insert_event(db_session, uuid.uuid4(), {"phone": "+1 (415) 555-0132"})
    result = await resolve_svc.resolve_identity(db_session, ev)
    assert result["status"] == "linked"
    assert result["rule"] == "exact_phone"


async def test_same_email_reuses_identity(db_session):
    import uuid

    e1 = await _insert_event(db_session, uuid.uuid4(), {"email": "a@example.com"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    e2 = await _insert_event(db_session, uuid.uuid4(), {"email": "A@example.com"})  # case-insensitive
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r1["identity_id"] == r2["identity_id"]


# --- Fuzzy: above-threshold auto-links, below-threshold goes to manual review ---
async def test_fuzzy_above_threshold_auto_links(db_session):
    import uuid

    # Seed an existing identity, then a returning contact with an identical name
    # fuzzy-matches at score 1.00 (>= threshold) and auto-links to it.
    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"

    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "linked"
    assert r2["identity_id"] == r1["identity_id"]


async def test_fuzzy_below_threshold_goes_to_manual_review(db_session):
    import uuid

    # Seed "Ada Lovelace", then send a name sharing one token ("Ada") but
    # otherwise different -> similar but below threshold -> manual review, never
    # force-merged.
    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"

    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Rutherford"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    entry = (
        await db_session.execute(select(ManualReviewQueue).where(
            ManualReviewQueue.event_id == e2.id))
    ).scalars().first()
    assert entry.status == "pending"
    assert "fuzzy_name_match_below_threshold" in entry.reason


# --- No identity fields parks in review ---------------------------------------
async def test_no_identity_fields_goes_to_manual_review(db_session):
    import uuid

    ev = await _insert_event(db_session, uuid.uuid4(), {})
    result = await resolve_svc.resolve_identity(db_session, ev)
    assert result["status"] == "queued_review"
    assert result["candidate_identity_id"] is None


# --- Never force-merge below threshold (the "impossible to do" invariant) -----
async def test_no_code_path_auto_merges_below_threshold(db_session):
    import uuid

    # Seed an existing identity.
    e1 = await _insert_event(
        db_session, uuid.uuid4(), {"name": "Ada Lovelace"}
    )
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"

    # A similar-but-below-threshold name (shares only "Ada") must never produce
    # a link — only a manual-review entry.
    e2 = await _insert_event(
        db_session, uuid.uuid4(), {"name": "Ada Zorp"}
    )
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    # Confirm NO identity_link was created for e2 (a merge would have one).
    links = (
        await db_session.execute(select(IdentityLink).where(IdentityLink.event_id == e2.id))
    ).scalars().all()
    assert links == []


# --- Manual review resolve endpoint (merge_into / create_new) -----------------
async def test_manual_review_resolve_merge_into_links(db_session):
    import uuid

    # Seed an existing identity and park a second, ambiguous event in review.
    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)

    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Rutherford"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"

    result = await resolve_svc.resolve_review(
        db_session, r2["review_id"], "merge_into", r1["identity_id"]
    )
    assert result["status"] == "resolved"
    assert result["identity_id"] == r1["identity_id"]

    entry = (
        await db_session.execute(select(ManualReviewQueue).where(
            ManualReviewQueue.id == r2["review_id"]))
    ).scalars().first()
    assert entry.status == "resolved"
    # A link now exists for e2 pointing at the merged identity.
    links = (
        await db_session.execute(select(IdentityLink).where(IdentityLink.event_id == e2.id))
    ).scalars().all()
    assert len(links) == 1
    assert links[0].identity_id == r1["identity_id"]


async def test_manual_review_resolve_create_new(db_session):
    import uuid

    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Rutherford"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"

    identities_before = len((await db_session.execute(select(Identity))).scalars().all())
    result = await resolve_svc.resolve_review(db_session, r2["review_id"], "create_new")
    assert result["status"] == "resolved"
    identities_after = len((await db_session.execute(select(Identity))).scalars().all())
    assert identities_after == identities_before + 1


async def test_resolve_missing_review_raises(db_session):
    import uuid

    try:
        await resolve_svc.resolve_review(db_session, uuid.uuid4(), "create_new")
        assert False, "expected LookupError for missing review"
    except LookupError:
        pass