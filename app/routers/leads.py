"""Leads API router — GET /api/v1/leads and /leads/{id} (FR-6, FR-7).

Read-only views over leads + their latest route + the latest score for the
source event. ``escalated`` is computed-on-read (sla_deadline < now()); no
scheduler is added (per PRD §12 open item). The first read that observes a breach
persists the flag and writes an ``escalated`` receipt — see
app/services/escalation.py for why the read path is allowed that one write.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AttributionTouch, Event, Lead, Route, Score
from app.db.session import get_db_session
from app.services.escalation import evaluate_escalation

router = APIRouter(prefix="/api/v1", tags=["leads"])


async def _latest_route(db: AsyncSession, lead_id: uuid.UUID) -> Route | None:
    """The lead's route. ``routes.lead_id`` is UNIQUE (migration 0009), so there is
    at most one; the ordering is retained only as a deterministic tie-break for
    databases created before that constraint landed."""
    return (
        await db.execute(
            select(Route)
            .where(Route.lead_id == lead_id)
            .order_by(Route.assigned_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def _latest_score(db: AsyncSession, event_id: uuid.UUID) -> Score | None:
    """Most recent score for the event that created/updated the lead."""
    return (
        await db.execute(
            select(Score)
            .where(Score.event_id == event_id)
            .order_by(Score.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


def _lead_dict(lead: Lead, route: Route | None, score: Score | None,
               escalated: bool = False) -> dict:
    return {
        "lead_id": str(lead.id),
        "identity_id": str(lead.identity_id),
        "status": lead.status,
        "source_event_id": str(lead.source_event_id),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
        "queue": route.queue if route else None,
        "rule_matched": route.rule_matched if route else None,
        "sla_deadline": route.sla_deadline.isoformat() if route else None,
        "escalated": escalated,
        "score": score.score if score else None,
        "decision": score.decision if score else None,
    }


@router.get("/leads", response_model=list)
async def list_leads(
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List leads, optionally filtered by status / source / decision.

    Returns an empty list when no filters match (never 404). ``source`` filters
    on the source event's connector; ``decision`` on the latest score for the
    source event. ``escalated`` is evaluated on read (see
    app/services/escalation.py) and a newly-observed SLA breach is persisted +
    receipted in a single commit at the end of the request.
    """
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    leads = (await db.execute(stmt)).scalars().all()

    results: list[dict] = []
    for lead in leads:
        route = await _latest_route(db, lead.id)
        score = await _latest_score(db, lead.source_event_id)
        if source:
            ev = (
                await db.execute(select(Event).where(Event.id == lead.source_event_id))
            ).scalars().first()
            if ev is None or ev.source != source:
                continue
        if decision:
            if score is None or score.decision != decision:
                continue
        escalated = await evaluate_escalation(db, route, lead.identity_id)
        results.append(_lead_dict(lead, route, score, escalated))
    await db.commit()  # persists any escalated flags + `escalated` receipts
    return results


@router.get("/leads/{lead_id}", response_model=dict)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Full lead detail: lead + latest route + latest score for source event."""
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id))
    ).scalars().first()
    if lead is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})

    route = await _latest_route(db, lead.id)
    score = await _latest_score(db, lead.source_event_id)
    touch = (
        await db.execute(
            select(AttributionTouch).where(AttributionTouch.identity_id == lead.identity_id)
        )
    ).scalars().first()
    escalated = await evaluate_escalation(db, route, lead.identity_id)
    await db.commit()  # persists a newly-observed breach + its `escalated` receipt

    return {
        **_lead_dict(lead, route, score, escalated),
        "route_id": str(route.id) if route else None,
        "assigned_at": route.assigned_at.isoformat() if route else None,
        "score_features": score.features if score else None,
        "policy_version": score.policy_version if score else None,
        "first_touch_at": touch.first_touch_at.isoformat() if touch else None,
        "first_touch_source": touch.first_touch_source if touch else None,
        "last_touch_at": touch.last_touch_at.isoformat() if touch else None,
        "last_touch_source": touch.last_touch_source if touch else None,
    }