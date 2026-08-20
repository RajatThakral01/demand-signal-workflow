"""Unit tests — discriminated-union schema validation across the 3 SIMULATED
sources (FR-1). Valid + invalid cases for each source."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.events import (
    EmailEngagementEvent,
    SocialMentionEvent,
    WebFormEvent,
    event_adapter,
)

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _web_form(**overrides):
    payload = {
        "source": "web_form",
        "external_event_id": "wf-0001",
        "received_at": NOW.isoformat(),
        "consent": True,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "company": "Analytical Engines",
        "message": "Interested in your pricing tiers",
    }
    payload.update(overrides)
    return payload


def _social_mention(**overrides):
    payload = {
        "source": "social_mention",
        "external_event_id": "sm-0001",
        "received_at": NOW.isoformat(),
        "display_name": "builders",
        "handle": "@builders",
        "text": "Anyone tried this product for our scale?",
        "topic": "evaluating",
    }
    payload.update(overrides)
    return payload


def _email_engagement(**overrides):
    payload = {
        "source": "email_engagement",
        "external_event_id": "em-0001",
        "received_at": NOW.isoformat(),
        "name": "Marie Curie",
        "email": "marie@example.com",
        "engagement_type": "reply",
        "campaign_id": "campaign-launch",
    }
    payload.update(overrides)
    return payload


SOURCES = {
    "web_form": (_web_form, WebFormEvent),
    "social_mention": (_social_mention, SocialMentionEvent),
    "email_engagement": (_email_engagement, EmailEngagementEvent),
}


@pytest.mark.parametrize("source", SOURCES.keys())
def test_valid_event_parses(source):
    factory, cls = SOURCES[source]
    model = event_adapter.validate_python(factory())
    assert isinstance(model, cls)
    assert model.source == source
    assert model.schema_version == "1.0"


@pytest.mark.parametrize("source", SOURCES.keys())
def test_missing_external_event_id_is_invalid(source):
    factory, _ = SOURCES[source]
    with pytest.raises(ValidationError):
        event_adapter.validate_python(factory(external_event_id=""))


@pytest.mark.parametrize("source", SOURCES.keys())
def test_missing_source_breaks_discrimination(source):
    factory, _ = SOURCES[source]
    with pytest.raises(ValidationError):
        event_adapter.validate_python(factory(source="mystery_source"))


def test_web_form_bad_email_invalid():
    with pytest.raises(ValidationError):
        event_adapter.validate_python(_web_form(email="not-an-email"))


def test_email_engagement_bad_engagement_type_invalid():
    with pytest.raises(ValidationError):
        event_adapter.validate_python(_email_engagement(engagement_type="bounced"))


def test_schema_version_is_tracked():
    model = event_adapter.validate_python(_social_mention(schema_version="2.0"))
    assert model.schema_version == "2.0"