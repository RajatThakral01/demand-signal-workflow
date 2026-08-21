"""Outbound response schemas (PRD §6 API Design / Error States table)."""

from pydantic import BaseModel


class EventIngestResponse(BaseModel):
    """Shape returned by ``POST /api/v1/events``.

    Mirrors the PRD's Error States table: an accepted-but-invalid event returns
    ``event_id`` + ``is_valid=False`` + ``invalid_reason`` (200); a true
    duplicate returns ``duplicate=True`` with the original result. Fields are
    optional because the exact set present depends on which branch fired.
    """

    event_id: str
    is_valid: bool = True
    invalid_reason: str | None = None
    duplicate: bool = False
    is_edit: bool = False
    # Pipeline state set by resolution (Phase 2). ``status`` is ``linked`` or
    # ``manual_review``; the latter parks the event (PRD Error States) until
    # resolved via POST /api/v1/manual-review/{id}/resolve.
    status: str | None = None
    review_id: str | None = None
    identity_id: str | None = None
    # Classification state (Phase 3, LIVE OpenRouter). ``interpret_status`` is
    # ``ok`` (or ``skipped`` for sub-min-length text) or ``error`` (provider
    # failure after bounded retries — visible, not a silent unknown). The label
    # and interpretation id are present on ok/skipped. cost/tokens are not
    # exposed here; see interpretations.token_usage.
    interpret_status: str | None = None
    label: str | None = None
    interpretation_id: str | None = None
    # Dead-letter state (Phase 8b, FR-11). When interpretation exhausts its bounded
    # retries, the event is dead-lettered: status="dead_letter" and stage names the
    # failing pipeline stage (e.g. "interpret"). No score/lead/route is produced.
    stage: str | None = None
    # Scoring state (Phase 4). ``score`` is null when label=unknown (needs_review).
    score: int | None = None
    decision: str | None = None
    score_id: str | None = None
    # Act state (Phase 5). ``lead_op`` is "created" or "updated"; ``queue`` /
    # ``rule_matched`` / ``sla_deadline`` describe the routing decision.
    lead_id: str | None = None
    lead_op: str | None = None     # "created" or "updated"
    route_id: str | None = None
    queue: str | None = None
    rule_matched: str | None = None
    sla_deadline: str | None = None
    # Attribution state (Phase 6, FR-8). Id of the identity's attribution_touches
    # row (first/last-touch tracking) written in the same commit as lead+route.
    attribution_touch_id: str | None = None