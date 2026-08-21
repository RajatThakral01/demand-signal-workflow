"""Shared post-resolution pipeline runner (Flow 1 steps 4-6, Flow 3 step 4).

Three entry points need the identical downstream sequence once an event has a
resolved identity:

  * ingest  — ``POST /api/v1/events`` for an event that resolved immediately
  * resume  — ``POST /api/v1/manual-review/{id}/resolve`` for an event that was
              parked by an ambiguous identity match and has now been adjudicated
  * replay  — ``POST /api/v1/admin/replay/{event_id}`` for a dead-lettered event

Before this module the sequence was inlined in the ingest router and duplicated
in the admin router, and the manual-review router simply never ran it — a
resolved event received an identity and then stopped, with no interpretation,
score, lead, route or attribution touch ever produced (PRD Flow 3 step 4 and the
API table's "resumed pipeline result"). Keeping one implementation is what
guarantees a resumed or replayed event is scored and routed by exactly the same
code as a first-pass event.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Interpretation, Score
from app.services.act import act
from app.services.interpret import classify_event
from app.services.score import score_event


async def run_downstream(db: AsyncSession, event: Event, identity_id: Any) -> dict:
    """Run interpret -> score -> act for an event whose identity is resolved.

    Returns ``{"interpret", "interpretation", "score_row", "act_result"}``.

    Raises ``InterpretError`` when interpretation exhausts its bounded retries
    (FR-11). The dead-letter row and its ``dead_lettered`` receipt are written
    atomically inside ``classify_event`` before it raises, so callers only need to
    choose how to surface the failure — 202 on ingest/resume, 503 on replay — and
    must not write a dead-letter themselves.

    Every stage is idempotent against a DB-level unique constraint
    (``interpretations.event_id``, ``scores`` upsert, ``leads.identity_id``,
    ``routes.lead_id``, ``attribution_touches.identity_id``), so running this
    twice for the same event — an edited resubmission, a review resume, or a
    replay after partial success — updates in place instead of double-writing.
    """
    interpret = await classify_event(db, event)

    interp_obj = (
        await db.execute(
            select(Interpretation).where(Interpretation.event_id == event.id)
        )
    ).scalars().first()

    score_row: Score | None = None
    if interp_obj is not None:
        score_row = await score_event(db, event, identity_id, interp_obj)
        await db.commit()  # score row + `scored` receipt

    act_result = await act(db, event, identity_id, score_row)  # commits internally

    return {
        "interpret": interpret,
        "interpretation": interp_obj,
        "score_row": score_row,
        "act_result": act_result,
    }
