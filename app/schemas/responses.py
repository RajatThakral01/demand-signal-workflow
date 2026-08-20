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