"""Identity resolution service (FR-3, Flow 3).

Rule order, confidence threshold and fuzzy algorithm are read from the versioned
policy file ``identity_policy_v1.json`` — never hardcoded inline. Exact email /
exact normalized phone auto-link; a fuzzy name+company match is manual-review-only
and never auto-merged. A fuzzy score is a reviewer aid only; it is not approval
to merge two identities.
"""

import difflib
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Event, Identity, IdentityLink, ManualReviewQueue
from app.logging import get_logger
from app.services.receipts import write_receipt

logger = get_logger(__name__)

_POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"

_phone_nondigits = re.compile(r"\D")


class ReviewAlreadyResolvedError(Exception):
    """Raised when another request resolved a review before this one acquired it."""


def _load_policy() -> dict:
    """Load the active identity policy version from `app/policies/`."""
    path = _POLICY_DIR / settings.identity_policy_version
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def get_identity_policy() -> dict:
    """Public accessor for tests/dashboard to inspect the active policy."""
    return _load_policy()


def normalize_email(email: str | None) -> str | None:
    """Canonicalize an email: strip whitespace, lowercase (policy normalizers)."""
    if email is None:
        return None
    return email.strip().lower()


def normalize_phone(phone: str | None) -> str | None:
    """Canonicalize a phone (policy normalizers).

    Strip non-digits, then drop a leading US country code ``1`` only if the
    remaining length would exceed 10 digits (leaving a 10-digit number).
    """
    if phone is None:
        return None
    digits = _phone_nondigits.sub("", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def fuzzy_similarity(
    name_a: str,
    name_b: str,
    company_a: str | None = None,
    company_b: str | None = None,
) -> Decimal:
    """Similarity in [0,1] for the fuzzy_name_company rule.

    Deterministic and stdlib-only: compare normalized name tokens and, when both
    values are known, normalized company tokens, then average the two ratios.
    Missing company data leaves a name-only *candidate suggestion*; it can never
    authorize an automatic merge.
    """
    if not name_a or not name_b:
        return Decimal("0")
    def _ratio(left: str, right: str) -> float:
        return difflib.SequenceMatcher(
            None, tuple(left.strip().lower().split()), tuple(right.strip().lower().split())
        ).ratio()

    name_ratio = _ratio(name_a, name_b)
    if company_a and company_b:
        return Decimal((name_ratio + _ratio(company_a, company_b)) / 2).quantize(
            Decimal("0.01")
        )
    return Decimal(name_ratio).quantize(Decimal("0.01"))


def should_auto_link(score: Decimal, threshold: Decimal) -> bool:
    """Return false for fuzzy matches; humans resolve every such candidate.

    Kept as a small compatibility helper for callers/tests that inspect the
    policy boundary. Exact email and phone links bypass this fuzzy-only helper.
    """
    del score, threshold
    return False


def _identity_name(identity: Identity) -> str:
    return (identity.display_name or identity.primary_email or "").strip()


async def _find_by_email(db: AsyncSession, email: str) -> Identity | None:
    return (
        await db.execute(select(Identity).where(Identity.primary_email == email))
    ).scalars().first()


async def _find_by_phone(db: AsyncSession, phone: str) -> Identity | None:
    stmt = select(Identity).where(Identity.primary_phone == phone)
    return (await db.execute(stmt)).scalars().first()


async def _find_fuzzy_candidate(
    db: AsyncSession, name: str, company: str | None
) -> Identity | None:
    """Return the best existing identity for a fuzzy name match, if any.

    Ties are broken by oldest first; the returned identity is the top similarity
    candidate, or None if there are no existing identities to match against.
    """
    stmt = select(Identity).where(
        or_(Identity.display_name.isnot(None), Identity.primary_email.isnot(None))
    )
    identities = (await db.execute(stmt)).scalars().all()
    best: Identity | None = None
    best_score = Decimal("0")
    for ident in identities:
        score = fuzzy_similarity(name, _identity_name(ident), company, ident.primary_company)
        if score > best_score:
            best_score = score
            best = ident
    return best


async def _upsert_identity_link(
    db: AsyncSession,
    *,
    event_id: Any,
    identity_id: Any,
    match_confidence: Decimal,
    match_rule: str,
) -> IdentityLink:
    """Create or update the one canonical identity link for an event.

    The unique ``identity_links.event_id`` constraint is the final race guard;
    ordinary edits update the existing link in place rather than creating a
    second association.
    """
    link = (
        await db.execute(select(IdentityLink).where(IdentityLink.event_id == event_id))
    ).scalars().first()
    if link is None:
        link = IdentityLink(
            identity_id=identity_id,
            event_id=event_id,
            match_confidence=match_confidence,
            match_rule=match_rule,
        )
        db.add(link)
    else:
        link.identity_id = identity_id
        link.match_confidence = match_confidence
        link.match_rule = match_rule
    return link


async def _queue_review(
    db: AsyncSession, event_id: Any, candidate_identity_id: Any, reason: str, score: Decimal
) -> ManualReviewQueue:
    existing = (
        await db.execute(
            select(ManualReviewQueue).where(ManualReviewQueue.event_id == event_id)
        )
    ).scalars().first()
    if existing is not None:
        # An edited resubmission can still be unresolved. Keep one work item per
        # event and refresh the candidate/reason instead of violating the unique
        # event_id invariant or producing duplicate reviewer work.
        existing.candidate_identity_id = candidate_identity_id
        existing.reason = reason
        return existing

    start = time.monotonic()
    entry = ManualReviewQueue(
        event_id=event_id,
        candidate_identity_id=candidate_identity_id,
        reason=reason,
        status="pending",
    )
    db.add(entry)
    await db.flush()  # populate entry.id before the receipt references it
    await write_receipt(
        db,
        action_type="review_queued",
        entity_id=entry.id,
        entity_type="manual_review",
        event_id=event_id,
        metadata={"reason": reason},
    )
    logger.info(
        "identity_marked_for_review",
        input_id=str(event_id),
        decision="queued_review",
        reason=reason,
        action="review_queued",
        result="skipped",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        event_id=str(event_id),
        confidence=str(score),
    )
    return entry


async def _link_via_exact(
    db: AsyncSession,
    event: Any,
    match_rule: str,
    find: Any,
    value: str,
    create: Any,
) -> dict:
    """Link an event to an existing (or newly created) identity for an exact match.

    Concurrency-safe: two near-simultaneous inserts of the same email/phone both
    pass the initial SELECT with no matching identity; one INSERT wins and the
    loser hits the DB-level partial unique index (IntegrityError), rolls back,
    re-SELECTs the winner's row and links to it — mirroring ingest.create_event.
    """
    start = time.monotonic()
    fields = event.identity_fields or {}
    display_name = fields.get("name")
    # The event row is already committed by the ingest step, so its id is stable
    # and safe to hold across a rollback (rollback would otherwise expire the ORM
    # object, and a lazy re-read of event.id would raise MissingGreenlet).
    event_id = event.id
    identity = await find(db, value)
    if identity is None:
        identity = create(db, display_name)
        db.add(identity)
        created_new = True
        try:
            await db.flush()  # populate identity.id; may raise if we lost a race
        except IntegrityError:
            await db.rollback()  # expunges the tentative identity/expires the session
            identity = await find(db, value)  # fresh, non-expired row from the winner
            if identity is None:
                raise
            created_new = False  # the winner already receipted its own creation
        if created_new:
            await write_receipt(
                db,
                action_type="identity_created",
                entity_id=identity.id,
                entity_type="identity",
                event_id=event_id,
                identity_id=identity.id,
                metadata={"match_rule": match_rule, "match_confidence": "1.00"},
            )
            logger.info(
                "identity_created",
                input_id=str(event_id),
                decision="linked",
                reason=f"new identity created via exact {match_rule} match",
                action="identity_created",
                result="ok",
                error=None,
                timing_ms=round((time.monotonic() - start) * 1000, 2),
                identity_id=str(identity.id),
                match_rule=match_rule,
                email=identity.primary_email,  # PII — redacted by _pii_redactor
                name=identity.display_name,     # PII — redacted by _pii_redactor
            )
    await _upsert_identity_link(
        db,
        event_id=event_id,
        identity_id=identity.id,
        match_confidence=Decimal("1.00"),
        match_rule=match_rule,
    )
    await db.commit()
    return {"status": "linked", "identity_id": identity.id,
            "rule": match_rule, "confidence": Decimal("1.00")}


async def resolve_identity(db: AsyncSession, event: Any) -> dict:
    """Resolve an event to an identity, creating a link and (if new) an identity.

    Returns a descriptor dict regardless of outcome:
      - ``{"status": "linked", "identity_id": ..., "rule": ..., "confidence": ...}``
      - ``{"status": "queued_review", "review_id": ..., "candidate_identity_id": ...}``
    On the ``queued_review`` outcome the pipeline HALTS for this event
    (interpret/score/act must not run) until a reviewer resolves the entry.
    """
    start = time.monotonic()
    fields: dict = event.identity_fields or {}

    existing_link = (
        await db.execute(select(IdentityLink).where(IdentityLink.event_id == event.id))
    ).scalars().first()
    if existing_link is not None:
        # An edit re-runs classification/scoring/routing but retains its already
        # adjudicated canonical identity. Re-resolving could otherwise turn one
        # event into a second review item or a conflicting identity assignment.
        return {
            "status": "linked",
            "identity_id": existing_link.identity_id,
            "rule": existing_link.match_rule,
            "confidence": existing_link.match_confidence,
        }

    # Phase 1 events carry identity fields keyed by the Event model's
    # build_identity_fields (email/phone/name/display_name/handle/company).
    email = normalize_email(fields.get("email"))
    if email:
        return await _link_via_exact(
            db=db, event=event, match_rule="exact_email",
            find=_find_by_email, value=email,
            create=lambda db, nm: Identity(
                primary_email=email, display_name=nm, primary_company=fields.get("company")
            ),
        )

    phone = normalize_phone(fields.get("phone"))
    if phone:
        return await _link_via_exact(
            db=db, event=event, match_rule="exact_phone",
            find=_find_by_phone, value=phone,
            create=lambda db, nm: Identity(
                primary_phone=phone, display_name=nm, primary_company=fields.get("company")
            ),
        )

    name = (fields.get("name") or fields.get("display_name") or "").strip()
    if name:
        company = (fields.get("company") or "").strip() or None
        candidate = await _find_fuzzy_candidate(db, name, company)
        if candidate is None:
            # No existing identities to be ambiguous with -> create fresh.
            identity = Identity(display_name=name, primary_company=company)
            db.add(identity)
            await db.flush()  # populate identity.id before linking
            await write_receipt(
                db,
                action_type="identity_created",
                entity_id=identity.id,
                entity_type="identity",
                event_id=event.id,
                identity_id=identity.id,
                metadata={"match_rule": "fuzzy_name_company", "match_confidence": "1.00"},
            )
            logger.info(
                "identity_created",
                input_id=str(event.id),
                decision="linked",
                reason="new identity created via fuzzy_name_company (no candidates)",
                action="identity_created",
                result="ok",
                error=None,
                timing_ms=round((time.monotonic() - start) * 1000, 2),
                identity_id=str(identity.id),
                match_rule="fuzzy_name_company",
                name=identity.display_name,  # PII — redacted by _pii_redactor
            )
            await _upsert_identity_link(
                db,
                event_id=event.id,
                identity_id=identity.id,
                match_confidence=Decimal("1.00"),
                match_rule="fuzzy_name_company",
            )
            await db.commit()
            await db.refresh(identity)
            return {"status": "linked", "identity_id": identity.id,
                    "rule": "fuzzy_name_company", "confidence": Decimal("1.00")}
        score = fuzzy_similarity(name, _identity_name(candidate), company, candidate.primary_company)
        # Enforce the versioned confidence_threshold: only scores at or above the
        # threshold propose a candidate to the reviewer; below-threshold scores are
        # still queued for review (there is no email/phone to auto-link on) but
        # with no suggested candidate. Auto-merge must NEVER happen regardless
        # of score (see should_auto_link which always returns False).
        policy = _load_policy()
        threshold = Decimal(str(policy.get("confidence_threshold", "0.85")))
        if score < threshold:
            reason = "no_confident_fuzzy_candidate"
            entry = await _queue_review(db, event.id, None, reason, score)
            await db.commit()
            await db.refresh(entry)
            return {"status": "queued_review", "review_id": entry.id,
                    "candidate_identity_id": None, "confidence": score}
        else:
            reason = f"fuzzy_name_company_manual_review:{score}"
            entry = await _queue_review(db, event.id, candidate.id, reason, score)
            await db.commit()
            await db.refresh(entry)
            return {"status": "queued_review", "review_id": entry.id,
                    "candidate_identity_id": candidate.id, "confidence": score}

    # No identity fields at all -> cannot resolve.
    reason = "no_identity_fields"
    entry = await _queue_review(db, event.id, None, reason, Decimal("0"))
    await db.commit()
    await db.refresh(entry)
    return {"status": "queued_review", "review_id": entry.id,
            "candidate_identity_id": None, "confidence": Decimal("0")}


async def get_pending_reviews(
    db: AsyncSession, status_filter: str | None = None
) -> list[ManualReviewQueue]:
    """Return manual-review entries, oldest first (FR-3, Flow 3).

    ``status_filter`` narrows the result (``pending`` / ``resolved``); defaults to
    ``pending`` when not supplied.
    """
    stmt = (
        select(ManualReviewQueue)
        .where(ManualReviewQueue.status == (status_filter or "pending"))
        .order_by(ManualReviewQueue.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def resolve_review(
    db: AsyncSession,
    review_id: Any,
    decision: str,
    identity_id: Any | None = None,
) -> dict:
    """Resolve a manual-review entry: ``merge_into`` an identity or ``create_new``.

    On resolution the entry is closed (``status="resolved"``, ``resolved_at`` set)
    and the event is linked to the chosen identity. The returned descriptor carries
    the resolved ``identity_id`` and the ``event_id`` so the caller can resume the
    halted pipeline (interpret -> score -> act) for that event.

    Both branches commit exactly once, so the review closure, the identity link,
    and every receipt land in a single transaction — a crash mid-resolution cannot
    leave a linked-but-unreceipted identity behind.

    Concurrency: two requests resolving the same review concurrently must yield
    exactly one winner (200) and one 409.  The claim is performed as an atomic
    ``UPDATE ... WHERE status='pending'`` — the database, not application-level
    SELECT-then-UPDATE, decides the winner.  ``SELECT FOR UPDATE`` alone was not
    sufficient under async ASGI concurrency where both handlers could snapshot the
    row as pending before either committed.
    """
    # Validate inputs before attempting to claim the review, so a bad request
    # never flips the review to resolved.
    if decision == "merge_into":
        if identity_id is None:
            raise ValueError("merge_into requires an identity_id")
        target = (
            await db.execute(select(Identity).where(Identity.id == identity_id))
        ).scalars().first()
        if target is None:
            raise LookupError("candidate identity not found")

    start = time.monotonic()
    resolved_at = datetime.now(timezone.utc)

    # Atomic claim — only the first writer that finds status='pending' succeeds.
    from sqlalchemy import update as sa_update

    claimed = await db.execute(
        sa_update(ManualReviewQueue)
        .where(ManualReviewQueue.id == review_id)
        .where(ManualReviewQueue.status == "pending")
        .values(status="resolved", resolved_at=resolved_at, resolution=decision)
        .returning(ManualReviewQueue.event_id, ManualReviewQueue.id)
    )
    row = claimed.fetchone()
    if row is None:
        # Either not found or already resolved — distinguish for correct HTTP code.
        exists = (
            await db.execute(select(ManualReviewQueue).where(ManualReviewQueue.id == review_id))
        ).scalars().first()
        if exists is None:
            raise LookupError("review not found")
        raise ReviewAlreadyResolvedError("review already resolved")

    event_id, entry_id = row

    if decision == "merge_into":
        # target already validated above; re-fetch to avoid stale identity after claim
        target = (
            await db.execute(select(Identity).where(Identity.id == identity_id))
        ).scalars().first()
        # Defensive: target could have been deleted between validation and claim
        if target is None:
            raise LookupError("candidate identity not found")
        await _upsert_identity_link(
            db,
            event_id=event_id,
            identity_id=target.id,
            match_confidence=Decimal("1.00"),
            match_rule="manual_review_resolve",
        )
        await write_receipt(
            db,
            action_type="review_resolved",
            entity_id=entry_id,
            entity_type="manual_review",
            event_id=event_id,
            identity_id=target.id,
            metadata={"resolution": decision, "review_id": str(entry_id)},
        )
        await db.commit()
        return {"status": "resolved", "identity_id": target.id,
                "review_id": review_id, "event_id": event_id}

    # create_new. The reviewer judged the fuzzy candidate to be a different person,
    # so this mints a brand-new canonical identity — and therefore owes an
    # `identity_created` receipt exactly like the two automatic creation paths
    # above (_link_via_exact and the fuzzy-no-candidate branch). Omitting it
    # desynchronized the identities <-> identity_created reconciliation pair the
    # first time a reviewer chose this branch, which fails FR-10 and Success
    # Criterion #2 (variance must be 0).
    #
    # The display name is carried over from the event's identity fields rather
    # than a "review_resolved" placeholder, which was leaking into API output as
    # if it were the contact's name. Manual review is only reachable via the
    # fuzzy-name or no-identity-fields paths, so there is never an email/phone to
    # promote here — display_name is the only field available, and may be None.
    event = (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalars().first()
    fields: dict = (event.identity_fields or {}) if event is not None else {}
    display_name = (fields.get("name") or fields.get("display_name") or "").strip() or None
    primary_company = (fields.get("company") or "").strip() or None

    new_identity = Identity(display_name=display_name, primary_company=primary_company)
    db.add(new_identity)
    await db.flush()  # populate new_identity.id before the link/receipts reference it
    await write_receipt(
        db,
        action_type="identity_created",
        entity_id=new_identity.id,
        entity_type="identity",
        event_id=event_id,
        identity_id=new_identity.id,
        metadata={"match_rule": "manual_review_resolve", "match_confidence": "1.00",
                  "review_id": str(review_id)},
    )
    logger.info(
        "identity_created",
        input_id=str(event_id),
        decision="linked",
        reason="new identity created by manual-review resolution (create_new)",
        action="identity_created",
        result="ok",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        identity_id=str(new_identity.id),
        match_rule="manual_review_resolve",
        name=new_identity.display_name,  # PII — redacted by _pii_redactor
    )
    await _upsert_identity_link(
        db,
        event_id=event_id,
        identity_id=new_identity.id,
        match_confidence=Decimal("1.00"),
        match_rule="manual_review_resolve",
    )
    await write_receipt(
        db,
        action_type="review_resolved",
        entity_id=entry_id,
        entity_type="manual_review",
        event_id=event_id,
        identity_id=new_identity.id,
        metadata={"resolution": decision, "review_id": str(entry_id)},
    )
    await db.commit()
    return {"status": "resolved", "identity_id": new_identity.id,
            "review_id": review_id, "event_id": event_id}
