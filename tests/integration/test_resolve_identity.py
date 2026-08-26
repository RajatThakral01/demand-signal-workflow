"""Integration tests — identity resolution + manual review flow (FR-3, Flow 3).

Runs against a real test Postgres: email/phone auto-link, fuzzy name+company
candidate → manual review (pipeline halts), the resolve endpoint (merge_into /
create_new), and the "never force-merge fuzzy candidates" invariant exercised via
a real resolution call.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Identity, IdentityLink, ManualReviewQueue
from app.services import resolve as resolve_svc

import uuid

from app.db.session import get_session_factory

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _event(**overrides):
    base = {
        "id": None,  # set by caller
        "identity_fields": {},
    }
    return base | overrides


async def _insert_event(db, event_id, identity_fields, source="web_form", ext=None):
    from app.db.models import Event
    from app.services.ingest import compute_dedupe_key, compute_payload_hash

    eid = ext or str(event_id)
    ev = Event(
        id=event_id,
        external_event_id=eid,  # unique dedupe key per event
        source=source,
        dedupe_key=compute_dedupe_key(source, eid),
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

    ev = await _insert_event(db_session, uuid.uuid4(), {"email": "ada@example.com", "name": "Ada"})
    result = await resolve_svc.resolve_identity(db_session, ev)
    assert result["status"] == "linked"
    assert result["rule"] == "exact_email"
    assert result["confidence"] == 1.00

    links = (await db_session.execute(select(IdentityLink))).scalars().all()
    assert len(links) == 1
    assert links[0].match_rule == "exact_email"


async def test_exact_phone_auto_links(db_session):

    ev = await _insert_event(db_session, uuid.uuid4(), {"phone": "+1 (415) 555-0132"})
    result = await resolve_svc.resolve_identity(db_session, ev)
    assert result["status"] == "linked"
    assert result["rule"] == "exact_phone"


async def test_same_email_reuses_identity(db_session):

    e1 = await _insert_event(db_session, uuid.uuid4(), {"email": "a@example.com"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    e2 = await _insert_event(db_session, uuid.uuid4(), {"email": "A@example.com"})  # case-insensitive
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r1["identity_id"] == r2["identity_id"]


# --- Fuzzy candidates always go to manual review ------------------------------
async def test_fuzzy_high_confidence_candidate_still_goes_to_manual_review(db_session):

    # Even an identical name+company is only a review candidate: the policy's
    # auto_link flag is false and a human must select merge_into/create_new.
    e1 = await _insert_event(
        db_session, uuid.uuid4(), {"name": "Ada Lovelace", "company": "Analytical Engines"}
    )
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"

    e2 = await _insert_event(
        db_session, uuid.uuid4(), {"name": "Ada Lovelace", "company": "Analytical Engines"}
    )
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    assert r2["candidate_identity_id"] == r1["identity_id"]
    links = (
        await db_session.execute(select(IdentityLink).where(IdentityLink.event_id == e2.id))
    ).scalars().all()
    assert links == []


async def test_fuzzy_below_threshold_goes_to_manual_review(db_session):

    # Seed "Ada Lovelace", then send a name sharing one token ("Ada") but
    # otherwise different -> similarity 0.50 < 0.85 -> manual review, but with
    # NO confident candidate (Fix 1).
    e1 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Lovelace"})
    r1 = await resolve_svc.resolve_identity(db_session, e1)
    assert r1["status"] == "linked"

    e2 = await _insert_event(db_session, uuid.uuid4(), {"name": "Ada Rutherford"})
    r2 = await resolve_svc.resolve_identity(db_session, e2)
    assert r2["status"] == "queued_review"
    assert r2["candidate_identity_id"] is None
    entry = (
        await db_session.execute(select(ManualReviewQueue).where(
            ManualReviewQueue.event_id == e2.id))
    ).scalars().first()
    assert entry.status == "pending"
    assert entry.candidate_identity_id is None
    assert entry.reason == "no_confident_fuzzy_candidate"


# --- No identity fields parks in review ---------------------------------------
async def test_no_identity_fields_goes_to_manual_review(db_session):

    ev = await _insert_event(db_session, uuid.uuid4(), {})
    result = await resolve_svc.resolve_identity(db_session, ev)
    assert result["status"] == "queued_review"
    assert result["candidate_identity_id"] is None


# --- Never force-merge below threshold (the "impossible to do" invariant) -----
async def test_no_code_path_auto_merges_below_threshold(db_session):

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


async def test_re_resolving_an_edited_event_keeps_one_identity_link(db_session):
    event = await _insert_event(db_session, uuid.uuid4(), {"email": "edit@example.com"})
    first = await resolve_svc.resolve_identity(db_session, event)
    second = await resolve_svc.resolve_identity(db_session, event)
    assert second["identity_id"] == first["identity_id"]
    links = (
        await db_session.execute(select(IdentityLink).where(IdentityLink.event_id == event.id))
    ).scalars().all()
    assert len(links) == 1


# --- Manual review resolve endpoint (merge_into / create_new) -----------------
async def test_manual_review_resolve_merge_into_links(db_session):

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
    try:
        await resolve_svc.resolve_review(db_session, uuid.uuid4(), "create_new")
        assert False, "expected LookupError for missing review"
    except LookupError:
        pass


# --- Concurrency: two simultaneous resolves of the same email/phone -------------
async def _concurrent_resolve(identity_fields):
    """Fire two resolve_identity calls (own event + own session) near-simultaneously
    for the same contact, returning the two results."""
    import asyncio
    factory = get_session_factory()

    async def attempt(n):
        async with factory() as s:
            ev = await _insert_event(s, uuid.uuid4(), identity_fields,
                                     source="web_form", ext=f"conc-{n}")
            return await resolve_svc.resolve_identity(s, ev)

    return await asyncio.gather(attempt(1), attempt(2))


async def test_concurrent_email_resolution_creates_one_identity(db_engine):
    r1, r2 = await _concurrent_resolve({"email": "race@example.com"})
    # Both events ended up linked (not failed) and to the SAME identity.
    assert r1["status"] == "linked"
    assert r2["status"] == "linked"
    assert r1["identity_id"] == r2["identity_id"]

    async with get_session_factory()() as s:
        identities = (await s.execute(select(Identity))).scalars().all()
        assert len(identities) == 1, "two canonical identities were created for one email"
        links = (await s.execute(select(IdentityLink))).scalars().all()
        assert len(links) == 2
        assert all(link.identity_id == identities[0].id for link in links)


async def test_concurrent_phone_resolution_creates_one_identity(db_engine):
    r1, r2 = await _concurrent_resolve({"phone": "+1 (415) 555-0199"})
    assert r1["status"] == "linked"
    assert r2["status"] == "linked"
    assert r1["identity_id"] == r2["identity_id"]

    async with get_session_factory()() as s:
        identities = (await s.execute(select(Identity))).scalars().all()
        assert len(identities) == 1, "two canonical identities were created for one phone"
        links = (await s.execute(select(IdentityLink))).scalars().all()
        assert len(links) == 2
        assert all(link.identity_id == identities[0].id for link in links)
