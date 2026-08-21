"""HTML pages router — evaluator dashboard (PRD §8, Phase 9).

Server-rendered Jinja2, plain semantic HTML, no JS framework, no build step.
Read-only except the manual-review resolve form (which posts to the JSON
service and shows the resumed pipeline result).  Every count shown traces
back to DB rows → receipts, and the reconciliation badge is live from the
JSON endpoint, not hardcoded.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pathlib import Path

from app.db.models import AttributionTouch, Event, Lead, Route, Score
from app.db.session import get_db_session
from app.routers.dashboard import reconciliation as dashboard_reconciliation
from app.services.escalation import evaluate_escalation
from app.services.summarize import get_summary

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["pages"])


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    summary = await get_summary(db)
    # Reuse the real reconciliation logic so badge is live
    recon = await dashboard_reconciliation(db=db, since=None, until=None)
    return templates.TemplateResponse(request, "dashboard_summary.html", {"summary": summary, "recon": recon})


@router.get("/dashboard/leads", response_class=HTMLResponse, include_in_schema=False)
async def leads_page(
    request: Request,
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
):
    # Replicate app/routers/leads.py logic but render HTML
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    leads = (await db.execute(stmt)).scalars().all()

    results: list[dict] = []
    for lead in leads:
        route = (
            await db.execute(select(Route).where(Route.lead_id == lead.id).order_by(Route.assigned_at.desc()).limit(1))
        ).scalars().first()
        score = (
            await db.execute(select(Score).where(Score.event_id == lead.source_event_id).order_by(Score.created_at.desc()).limit(1))
        ).scalars().first()
        if source:
            ev = (await db.execute(select(Event).where(Event.id == lead.source_event_id))).scalars().first()
            if ev is None or ev.source != source:
                continue
        if decision:
            if score is None or score.decision != decision:
                continue
        escalated = await evaluate_escalation(db, route, lead.identity_id)
        results.append({
            "lead_id": str(lead.id),
            "identity_id": str(lead.identity_id),
            "status": lead.status,
            "queue": route.queue if route else None,
            "rule_matched": route.rule_matched if route else None,
            "sla_deadline": route.sla_deadline.isoformat() if route and route.sla_deadline else None,
            "score": score.score if score else None,
            "decision": score.decision if score else None,
            "escalated": escalated,
        })
    await db.commit()
    return templates.TemplateResponse(request, "leads_list.html", {"leads": results, "filters": {"status": status, "source": source, "decision": decision}})


@router.get("/dashboard/leads/{lead_id}", response_class=HTMLResponse, include_in_schema=False)
async def lead_detail_page(lead_id: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalars().first()
    if lead is None:
        return HTMLResponse(content="<h1>404 Lead not found</h1>", status_code=404)
    route = (
        await db.execute(select(Route).where(Route.lead_id == lead.id).order_by(Route.assigned_at.desc()).limit(1))
    ).scalars().first()
    score = (
        await db.execute(select(Score).where(Score.event_id == lead.source_event_id).order_by(Score.created_at.desc()).limit(1))
    ).scalars().first()
    touch = (
        await db.execute(select(AttributionTouch).where(AttributionTouch.identity_id == lead.identity_id))
    ).scalars().first()
    event = (await db.execute(select(Event).where(Event.id == lead.source_event_id))).scalars().first()
    escalated = await evaluate_escalation(db, route, lead.identity_id)
    await db.commit()

    lead_dict = {
        "lead_id": str(lead.id),
        "identity_id": str(lead.identity_id),
        "status": lead.status,
        "source_event_id": str(lead.source_event_id),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
        "queue": route.queue if route else None,
        "rule_matched": route.rule_matched if route else None,
        "sla_deadline": route.sla_deadline.isoformat() if route and route.sla_deadline else None,
        "escalated": escalated,
        "score": score.score if score else None,
        "decision": score.decision if score else None,
        "assigned_at": route.assigned_at.isoformat() if route and route.assigned_at else None,
        "score_features": score.features if score else None,
        "policy_version": score.policy_version if score else None,
        "first_touch_at": touch.first_touch_at.isoformat() if touch else None,
        "first_touch_source": touch.first_touch_source if touch else None,
        "last_touch_at": touch.last_touch_at.isoformat() if touch else None,
        "last_touch_source": touch.last_touch_source if touch else None,
    }
    event_dict = None
    if event is not None:
        event_dict = {
            "external_event_id": event.external_event_id,
            "source": event.source,
            "payload_hash": event.payload_hash,
            "is_edit": event.is_edit,
            "is_valid": event.is_valid,
            "received_at": event.received_at.isoformat() if event.received_at else None,
        }
    return templates.TemplateResponse(request, "lead_detail.html", {"lead": lead_dict, "event": event_dict})


@router.get("/dashboard/manual-review", response_class=HTMLResponse, include_in_schema=False)
async def manual_review_page(request: Request, db: AsyncSession = Depends(get_db_session)):
    from app.services.resolve import get_pending_reviews
    # Show all pending; also include recently resolved for context
    pending = await get_pending_reviews(db, "pending")
    resolved = await get_pending_reviews(db, "resolved")
    rows = pending + resolved
    # Keep pending first, then resolved, limit for HTML readability
    rows = sorted(rows, key=lambda r: (0 if r.status == "pending" else 1, str(r.id)))
    view = [
        {
            "id": str(r.id),
            "event_id": str(r.event_id),
            "candidate_identity_id": str(r.candidate_identity_id) if r.candidate_identity_id else None,
            "reason": r.reason,
            "status": r.status,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows[:100]
    ]
    return templates.TemplateResponse(request, "manual_review.html", {"rows": view})


@router.post("/dashboard/manual-review/{review_id}/resolve", include_in_schema=False)
async def manual_review_resolve_form(
    review_id: str,
    request: Request,
    decision: Annotated[str, Form(...)],
    identity_id: Annotated[str | None, Form()] = None,
    db: AsyncSession = Depends(get_db_session),
):
    from app.services.resolve import ReviewAlreadyResolvedError, resolve_review
    from app.services.interpret import InterpretError
    from app.services.pipeline import run_downstream
    from app.db.models import Event, ManualReviewQueue
    from sqlalchemy import select as sel

    # Validate review exists before claiming
    exists = (await db.execute(sel(ManualReviewQueue).where(ManualReviewQueue.id == review_id))).scalars().first()
    if exists is None:
        return HTMLResponse(content="<h1>404 review not found</h1>", status_code=404)

    try:
        result = await resolve_review(db, review_id, decision, identity_id)
    except ReviewAlreadyResolvedError:
        return HTMLResponse(content="<h1>409 review already resolved</h1>", status_code=409)
    except ValueError as exc:
        return HTMLResponse(content=f"<h1>400 {exc}</h1>", status_code=400)
    except LookupError as exc:
        return HTMLResponse(content=f"<h1>404 {exc}</h1>", status_code=404)

    # Resume pipeline as the JSON endpoint does
    event = (await db.execute(sel(Event).where(Event.id == result["event_id"]))).scalars().first()
    if event is not None:
        try:
            await run_downstream(db, event, result["identity_id"])
        except InterpretError:
            # Dead-lettered — still resolved
            pass

    # Redirect back to the queue so evaluator sees updated status
    return RedirectResponse(url="/dashboard/manual-review", status_code=303)


@router.get("/dashboard/dead-letter", response_class=HTMLResponse, include_in_schema=False)
async def dead_letter_page(
    request: Request,
    resolved: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
):
    from app.db.models import DeadLetterQueue
    stmt = (
        select(DeadLetterQueue)
        .where(DeadLetterQueue.resolved.is_(resolved))
        .order_by(DeadLetterQueue.created_at, DeadLetterQueue.id)
    )
    entries = (await db.execute(stmt)).scalars().all()
    view = [
        {
            "id": str(e.id),
            "event_id": str(e.event_id),
            "stage": e.stage,
            "error": e.error,
            "retry_count": e.retry_count,
            "resolved": e.resolved,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "replay_url": f"/api/v1/admin/replay/{e.event_id}",
        }
        for e in entries
    ]
    return templates.TemplateResponse(request, "dead_letter.html", {"rows": view, "resolved": resolved})
