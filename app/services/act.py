"""Act service — lead creation/update, routing, SLA (FR-6, FR-7, Phase 5).

Transaction contract: create_or_update_lead() and route_lead() both call
db.add() but do NOT commit. The caller (act()) flushes for the lead id, then
adds the route, then does a SINGLE await db.commit() covering both. This is the
Phase 5 atomic unit. Phase 7 will add a receipts write inside the same commit
without requiring a transaction restructure.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Lead, Route, Score
from app.logging import get_logger
from app.services.attribute import upsert_attribution

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
    score_row: Score,
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


async def route_lead(db: AsyncSession, lead: Lead, decision: str, label: str) -> Route:
    """Compute and persist a routing decision for a lead (no commit)."""
    rules = _load_routing_rules()
    queue, rule_matched, sla_hours = apply_routing_rule(decision, label, rules)
    now = datetime.now(timezone.utc)
    sla_deadline = now + timedelta(hours=sla_hours)
    route = Route(
        lead_id=lead.id,
        queue=queue,
        rule_matched=rule_matched,
        assigned_at=now,
        sla_deadline=sla_deadline,
    )
    db.add(route)
    logger.info(
        "lead_routed",
        lead_id=str(lead.id),
        queue=queue,
        rule_matched=rule_matched,
        sla_deadline=sla_deadline.isoformat(),
    )
    return route


async def act(
    db: AsyncSession,
    event: Event,
    identity_id: uuid.UUID,
    score_row: Score | None,
) -> dict:
    """Public entrypoint: create/update lead + route, single commit."""
    lead, lead_op = await create_or_update_lead(db, event, identity_id, score_row)
    decision = score_row.decision if score_row else "needs_review"
    label = (
        score_row.features.get("label", "unknown")
        if score_row and score_row.features
        else "unknown"
    )
    route = await route_lead(db, lead, decision, label)
    lead.status = "routed"
    # Flow: attribution (FR-8). Slots into the same transaction as lead+route.
    touch = await upsert_attribution(db, event, identity_id)
    # Receipt write will go here in Phase 7 — intentional TODO stub:
    # await write_receipt(db, action_type="lead_created"|"lead_updated", ...)
    await db.commit()  # ONE commit: lead + route + attribution together
    logger.info(
        "act_complete",
        event_id=str(event.id),
        lead_id=str(lead.id),
        lead_op=lead_op,
        queue=route.queue,
        decision=decision,
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