"""Versioned, source-specific event schemas (PRD §6 API Design / FR-1).

A discriminated union keyed on ``source`` so that a single ``/api/v1/events``
endpoint can validate all three synthetic connector shapes. ``schema_version``
rides on every event so a future schema change is auditable per row.
"""

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, EmailStr, Field, TypeAdapter


class EventCommon(BaseModel):
    """Fields shared by every source shape.

    ``external_event_id`` + ``source`` are the two inputs to the deterministic
    ``dedupe_key`` hash (FR-2). ``schema_version`` names the schema revision in
    use for this event.
    """

    external_event_id: str = Field(..., min_length=1)
    schema_version: str = "1.0"
    received_at: datetime
    consent: bool = False
    campaign_id: str | None = None


class WebFormEvent(EventCommon):
    """A web form submission (SIMULATED connector / fixture generator)."""

    source: Literal["web_form"]
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    company: str | None = None
    message: str | None = None


class SocialMentionEvent(EventCommon):
    """A community / social mention (SIMULATED connector / fixture generator)."""

    source: Literal["social_mention"]
    display_name: str | None = None
    handle: str | None = None
    text: str | None = None
    topic: str | None = None


class EmailEngagementEvent(EventCommon):
    """An email / campaign engagement event (SIMULATED connector)."""

    source: Literal["email_engagement"]
    name: str | None = None
    email: EmailStr | None = None
    engagement_type: Literal["open", "click", "reply", "unsubscribe"] | None = None
    reply_body: str | None = None


EventIn = Annotated[
    Union[WebFormEvent, SocialMentionEvent, EmailEngagementEvent],
    Field(discriminator="source"),
]

event_adapter: TypeAdapter = TypeAdapter(EventIn)
"""TypeAdapter for the discriminated union — the canonical way to validate raw
JSON/dicts against ``EventIn`` (works with the Annotated/Union shape, unlike a
direct ``.validate_python`` call which the wrapping typing.Union does not expose)."""