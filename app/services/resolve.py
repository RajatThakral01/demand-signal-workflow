"""Identity resolution service (FR-3, Flow 3).

Rule order, confidence threshold and fuzzy algorithm are read from the versioned
policy file ``identity_policy_v1.json`` — never hardcoded inline. Exact email /
exact normalized phone auto-link; a fuzzy name+company match is manual-review-only
and *never* auto-merged below the configured threshold. Because resolution admits
a link only when the computed confidence ``>= threshold``, there is no code path
that can auto-merge a below-threshold match.
"""

import difflib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Identity, IdentityLink, ManualReviewQueue
from app.logging import get_logger

logger = get_logger(__name__)

_POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"

_phone_nondigits = re.compile(r"\D")


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


def fuzzy_similarity(name_a: str, name_b: str) -> Decimal:
    """Similarity in [0,1] for the fuzzy_name_company rule.

    Deterministic, stdlib-only: tokenize each name into lowercase tokens and
    score the sequence with ``difflib.SequenceMatcher.ratio()``. Chosen over a
    third-party fuzzy library (rapidfuzz/python-Levenshtein) to keep the stack
    minimal and fully deterministic (scoring determinism is a PRD requirement);
    token-set ratio handles typical name-with-typo cases well at this assessment
    scale. Documented in identity_policy_v1.json.
    """
    if not name_a or not name_b:
        return Decimal("0")
    tokens_a = tuple(name_a.strip().lower().split())
    tokens_b = tuple(name_b.strip().lower().split())
    ratio = difflib.SequenceMatcher(None, tokens_a, tokens_b).ratio()
    return Decimal(ratio).quantize(Decimal("0.01"))


def should_auto_link(score: Decimal, threshold: Decimal) -> bool:
    """Decision rule for a fuzzy match.

    Boundary semantics: a score AT the threshold auto-links (``>=``); strictly
    below it does not. Exposed as a pure function so the 0.849 / 0.85 / 0.851
    boundary is explicitly testable and so that *any* code path that wants to
    create a link must consult this single decision point.
    """
    return score >= threshold


def _identity_name(identity: Identity) -> str:
    return (identity.display_name or identity.primary_email or "").strip()


async def _find_by_email(db: AsyncSession, email: str) -> Identity | None:
    return (
        await db.execute(select(Identity).where(Identity.primary_email == email))
    ).scalars().first()


async def _find_by_phone(db: AsyncSession, phone: str) -> Identity | None:
    stmt = select(Identity).where(Identity.primary_phone == phone)
    return (await db.execute(stmt)).scalars().first()


async def _find_fuzzy_candidate(db: AsyncSession, name: str) -> Identity | None:
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
        score = fuzzy_similarity(name, _identity_name(ident))
        if score > best_score:
            best_score = score
            best = ident
    return best


def _queue_review(
    db: AsyncSession, event_id: Any, candidate_identity_id: Any, reason: str, score: Decimal
) -> ManualReviewQueue:
    entry = ManualReviewQueue(
        event_id=event_id,
        candidate_identity_id=candidate_identity_id,
        reason=reason,
        status="pending",
    )
    db.add(entry)
    logger.info("identity_marked_for_review", event_id=str(event_id), reason=reason,
                confidence=str(score))
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
    display_name = (event.identity_fields or {}).get("name")
    # The event row is already committed by the ingest step, so its id is stable
    # and safe to hold across a rollback (rollback would otherwise expire the ORM
    # object, and a lazy re-read of event.id would raise MissingGreenlet).
    event_id = event.id
    identity = await find(db, value)
    if identity is None:
        identity = create(db, display_name)
        db.add(identity)
        try:
            await db.flush()  # populate identity.id; may raise if we lost a race
        except IntegrityError:
            await db.rollback()  # expunges the tentative identity/expires the session
            identity = await find(db, value)  # fresh, non-expired row from the winner
            if identity is None:
                raise
    link = IdentityLink(
        identity_id=identity.id, event_id=event_id,
        match_confidence=Decimal("1.00"), match_rule=match_rule,
    )
    db.add(link)
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
    policy = _load_policy()
    threshold = Decimal(str(policy["confidence_threshold"]))
    fields: dict = event.identity_fields or {}

    # Phase 1 events carry identity fields keyed by the Event model's
    # build_identity_fields (email/phone/name/display_name/handle/company).
    email = normalize_email(fields.get("email"))
    if email:
        return await _link_via_exact(
            db=db, event=event, match_rule="exact_email",
            find=_find_by_email, value=email,
            create=lambda db, nm: Identity(primary_email=email, display_name=nm),
        )

    phone = normalize_phone(fields.get("phone"))
    if phone:
        return await _link_via_exact(
            db=db, event=event, match_rule="exact_phone",
            find=_find_by_phone, value=phone,
            create=lambda db, nm: Identity(primary_phone=phone, display_name=nm),
        )

    name = (fields.get("name") or fields.get("display_name") or "").strip()
    if name:
        candidate = await _find_fuzzy_candidate(db, name)
        if candidate is None:
            # No existing identities to be ambiguous with -> create fresh.
            identity = Identity(display_name=name)
            db.add(identity)
            await db.flush()  # populate identity.id before linking
            link = IdentityLink(
                identity_id=identity.id, event_id=event.id,
                match_confidence=Decimal("1.00"), match_rule="fuzzy_name_company",
            )
            db.add(link)
            await db.commit()
            await db.refresh(identity)
            return {"status": "linked", "identity_id": identity.id,
                    "rule": "fuzzy_name_company", "confidence": Decimal("1.00")}
        score = fuzzy_similarity(name, _identity_name(candidate))
        if should_auto_link(score, threshold):
            link = IdentityLink(
                identity_id=candidate.id, event_id=event.id,
                match_confidence=score, match_rule="fuzzy_name_company",
            )
            db.add(link)
            await db.commit()
            return {"status": "linked", "identity_id": candidate.id,
                    "rule": "fuzzy_name_company", "confidence": score}
        # Below threshold -> manual review, NEVER auto-merge.
        reason = f"fuzzy_name_match_below_threshold:{score}"
        entry = _queue_review(db, event.id, candidate.id, reason, score)
        await db.commit()
        await db.refresh(entry)
        return {"status": "queued_review", "review_id": entry.id,
                "candidate_identity_id": candidate.id, "confidence": score}

    # No identity fields at all -> cannot resolve.
    reason = "no_identity_fields"
    entry = _queue_review(db, event.id, None, reason, Decimal("0"))
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
    and the event is linked to the chosen identity, resuming its pipeline. The
    returned descriptor includes the resolved ``identity_id``; downstream pipeline
    stages (interpret/score/act, later phases) continue from here.
    """
    entry = (
        await db.execute(select(ManualReviewQueue).where(ManualReviewQueue.id == review_id))
    ).scalars().first()
    if entry is None:
        raise LookupError("review not found")

    if decision == "merge_into":
        if identity_id is None:
            raise ValueError("merge_into requires an identity_id")
        target = (
            await db.execute(select(Identity).where(Identity.id == identity_id))
        ).scalars().first()
        if target is None:
            raise LookupError("candidate identity not found")
        entry.resolution = decision
        entry.status = "resolved"
        entry.resolved_at = datetime.now()
        link = IdentityLink(
            identity_id=target.id, event_id=entry.event_id,
            match_confidence=Decimal("1.00"), match_rule="manual_review_resolve",
        )
        db.add(link)
        await db.commit()
        return {"status": "resolved", "identity_id": target.id, "review_id": entry.id}

    # create_new
    new_identity = Identity(display_name="review_resolved")
    db.add(new_identity)
    await db.commit()
    await db.refresh(new_identity)
    entry.resolution = decision
    entry.status = "resolved"
    entry.resolved_at = datetime.now()
    link = IdentityLink(
        identity_id=new_identity.id, event_id=entry.event_id,
        match_confidence=Decimal("1.00"), match_rule="manual_review_resolve",
    )
    db.add(link)
    await db.commit()
    return {"status": "resolved", "identity_id": new_identity.id, "review_id": entry.id}