"""Act service — lead creation/update, routing, SLA (FR-6, FR-7, Phase 5).

Transaction contract: create_or_update_lead() and route_lead() both call
db.add() but do NOT commit. The caller (act()) flushes for the lead id, then
adds the route, then does a SINGLE await db.commit() covering both. This is the
Phase 5 atomic unit. Phase 7 will add a receipts write inside the same commit
without requiring a transaction restructure.
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Lead, Route, Score
from app.logging import get_logger
from app.services.attribute import upsert_attribution
from app.services.receipts import write_receipt

logger = get_logger(__name__)

_POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"
_RULES_FILENAME = "routing_rules_v1.json"

# Module-level cache of the loaded routing rules, keyed by filename.
_RULES_CACHE: dict[str, dict | None] = {}


def _load_routing_rules() -> dict:
    """Load ``app/policies/{_RULES_FILENAME}`` (cached by filename)."""
    if _RULES_FILENAME in _RULES_CACHE and _RULES_CACHE[_RULES_FILENAME] is not None:
        return _RULES_CACHE[_RULES_FILENAME]
    path = _POLICY_DIR / _RULES_FILENAME
    if not path.exists():
        raise RuntimeError(f"routing rules file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            rules = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"routing rules file is invalid JSON: {path}") from exc
    _RULES_CACHE[_RULES_FILENAME] = rules
    return rules


def apply_routing_rule(decision: str, label: str, rules: dict) -> tuple[str, str, int]:
    """Pure function: return ``(queue, rule_matched, sla_hours)``.

    Iterates ``rules["rules"]`` in order; the first rule whose condition matches
    the decision (and optionally label) wins. If no rule matches, the fallback
    fires. No DB, no side effects — fully unit-testable in isolation.
    """
    for rule in rules["rules"]:
        cond = rule["condition"]
        if cond["decision"] != decision:
            continue
        if "label" in cond and cond["label"] != label:
            continue
        return rule["queue"], rule["name"], int(rule["sla_hours"])
    fallback = rules["fallback"]
    return fallback["queue"], fallback["rule_matched"], int(fallback["sla_hours"])


async def create_or_update_lead(
    db: AsyncSession,
    event: Event,
    identity_id: uuid.UUID,
    score_row: Score | None,
) -> tuple[Lead, str]:
    """Create a lead for the identity, or update the existing one.

    Returns ``(lead, "created" | "updated")``. identity_id is UNIQUE at the DB
    level: on a concurrent duplicate insert the loser gets IntegrityError and
    re-reads the winner's row. Does NOT commit — the caller owns the transaction.
    """
    existing = (
        await db.execute(select(Lead).where(Lead.identity_id == identity_id))
    ).scalars().first()
    if existing is not None:
        existing.source_event_id = event.id
        existing.status = "routed"
        return existing, "updated"

    lead = Lead(identity_id=identity_id, status="new", source_event_id=event.id)
    db.add(lead)
    try:
        await db.flush()  # populate lead.id
    except IntegrityError:
        await db.rollback()  # rolls back the flush, not a full commit
        winner = (
            await db.execute(select(Lead).where(Lead.identity_id == identity_id))
        ).scalars().first()
        if winner is None:
            raise  # unexpected — re-raise
        winner.source_event_id = event.id
        winner.status = "routed"
        return winner, "updated"
    return lead, "created"


async def route_lead(db: AsyncSession, lead: Lead, decision: str, label: str) -> tuple[Route, str]:
    """Compute and persist a routing decision for a lead (upsert by lead_id).

    Returns ``(route, "created" | "updated")``. One route row per lead (lead_id
    UNIQUE). If a route already exists for ``lead.id`` (e.g. an edited resubmission
    re-running interpret→score→act), it is UPDATED in place with the freshly computed
    queue/rule_matched/sla_deadline — the routing rules may yield a different queue
    if the score/decision changed. If none exists, a new route is inserted. assigned_at
    is preserved on update. Does NOT commit — the caller owns the transaction.
    """
    start = time.monotonic()
    rules = _load_routing_rules()
    queue, rule_matched, sla_hours = apply_routing_rule(decision, label, rules)
    now = datetime.now(timezone.utc)
    sla_deadline = now + timedelta(hours=sla_hours)

    existing = (
        await db.execute(select(Route).where(Route.lead_id == lead.id))
    ).scalars().first()
    if existing is not None:
        # Edit re-run / re-route: update the existing row in place.
        existing.queue = queue
        existing.rule_matched = rule_matched
        existing.sla_deadline = sla_deadline
        logger.info(
            "lead_routed",
            input_id=str(lead.id),
            decision=decision,
            reason=f"re-routed to queue={queue} via rule={rule_matched} (sla {sla_hours}h)",
            action="route_updated",
            result="ok",
            error=None,
            timing_ms=round((time.monotonic() - start) * 1000, 2),
            lead_id=str(lead.id),
            queue=queue,
            rule_matched=rule_matched,
            sla_deadline=sla_deadline.isoformat(),
        )
        return existing, "updated"

    route = Route(
        lead_id=lead.id,
        queue=queue,
        rule_matched=rule_matched,
        assigned_at=now,
        sla_deadline=sla_deadline,
    )
    db.add(route)
    try:
        await db.flush()  # populate route.id; may raise if we lost a race
    except IntegrityError:
        # Lost a concurrent insert race — the DB UNIQUE constraint won.
        await db.rollback()  # discards the tentative row; re-read the winner
        route = (
            await db.execute(select(Route).where(Route.lead_id == lead.id))
        ).scalars().first()
        if route is None:
            raise  # unexpected — re-raise
        route.queue = queue
        route.rule_matched = rule_matched
        route.sla_deadline = sla_deadline
        logger.info(
            "lead_routed",
            input_id=str(lead.id),
            decision=decision,
            reason=f"re-routed to queue={queue} via rule={rule_matched} (sla {sla_hours}h)",
            action="route_updated",
            result="ok",
            error=None,
            timing_ms=round((time.monotonic() - start) * 1000, 2),
            lead_id=str(lead.id),
            queue=queue,
            rule_matched=rule_matched,
            sla_deadline=sla_deadline.isoformat(),
        )
        return route, "updated"

    logger.info(
        "lead_routed",
        input_id=str(lead.id),
        decision=decision,
        reason=f"routed to queue={queue} via rule={rule_matched} (sla {sla_hours}h)",
        action="routed",
        result="ok",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        lead_id=str(lead.id),
        queue=queue,
        rule_matched=rule_matched,
        sla_deadline=sla_deadline.isoformat(),
    )
    return route, "created"


async def act(
    db: AsyncSession,
    event: Event,
    identity_id: uuid.UUID,
    score_row: Score | None,
) -> dict:
    """Public entrypoint: create/update lead + route, single commit."""
    start = time.monotonic()
    lead, lead_op = await create_or_update_lead(db, event, identity_id, score_row)
    decision = score_row.decision if score_row else "needs_review"
    label = (
        score_row.features.get("label", "unknown")
        if score_row and score_row.features
        else "unknown"
    )
    route, route_op = await route_lead(db, lead, decision, label)
    lead.status = "routed"
    # Flow: attribution (FR-8). Slots into the same transaction as lead+route.
    touch = await upsert_attribution(db, event, identity_id)

    await db.flush()  # populate route.id + touch.id before the receipts reference them

    # Lead receipt (FR-9)
    await write_receipt(
        db,
        action_type="lead_created" if lead_op == "created" else "lead_updated",
        entity_id=lead.id, entity_type="lead",
        event_id=event.id, identity_id=identity_id,
        metadata={"lead_op": lead_op, "decision": decision})

    # Route receipt — FR-9 ("nothing mutates state without a receipt"): a newly
    # created route writes "routed"; a route updated in place (re-route via an edit)
    # writes "route_updated". The reconciliation allowlist only counts action_type
    # "routed", so "route_updated" is invisible to it by construction (same as
    # lead_updated/attributed_updated).
    route_action = "routed" if route_op == "created" else "route_updated"
    await write_receipt(
        db,
        action_type=route_action,
        entity_id=route.id, entity_type="route",
        event_id=event.id, identity_id=identity_id,
        metadata={"queue": route.queue, "rule_matched": route.rule_matched,
                  "sla_deadline": route.sla_deadline.isoformat()})

    # Attribution receipt
    await write_receipt(
        db,
        action_type="attributed_created" if lead_op == "created" else "attributed_updated",
        entity_id=touch.id, entity_type="attribution_touch",
        event_id=event.id, identity_id=identity_id,
        metadata={"first_touch_at": touch.first_touch_at.isoformat(),
                  "last_touch_at": touch.last_touch_at.isoformat()})

    await db.commit()  # ONE commit: lead + route + attribution + three receipts
    logger.info(
        "act_complete",
        input_id=str(event.id),
        decision=decision,
        reason=f"lead {lead_op} routed to {route.queue} (rule {route.rule_matched}) with attribution",
        action="lead_created" if lead_op == "created" else "lead_updated",
        result="ok",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        lead_id=str(lead.id),
        lead_op=lead_op,
        queue=route.queue,
    )
    return {
        "lead_id": str(lead.id),
        "lead_op": lead_op,           # "created" or "updated"
        "route_id": str(route.id),
        "queue": route.queue,
        "rule_matched": route.rule_matched,
        "sla_deadline": route.sla_deadline.isoformat(),
        "decision": decision,
        "attribution_touch_id": str(touch.id),
    }