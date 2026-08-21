"""Integration tests — manual-review API endpoints (FR-3, Flow 3).

Exercises POST /api/v1/events landing an ambiguous event in manual review, then
GET /api/v1/manual-review?status=pending and POST /api/v1/manual-review/{id}/resolve
over HTTP, plus the router's status-code contract (404 / 409 / 400).
"""

from datetime import datetime, timezone

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _event(**overrides):
    payload = {
        "source": "social_mention",
        "external_event_id": "sm-review-1",
        "received_at": NOW.isoformat(),
        "display_name": "Ada-Rutherford-Ambiguous",
        "text": "evaluating the platform",
        "company": "Example",
    }
    payload.update(overrides)
    return payload


async def test_events_resolution_parks_ambiguous_in_manual_review(client):
    # No existing identity -> first event creates one (name-only fuzzy create).
    await client.post("/api/v1/events", json=_event(external_event_id="seed-1",
                                                    display_name="Ada Lovelace"))
    # A fuzzy name candidate always goes to manual review.
    resp = await client.post("/api/v1/events", json=_event(external_event_id="seed-2",
                                                           display_name="Ada Rutherford"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "manual_review"
    assert body["review_id"]


async def test_manual_review_list_pending_and_resolve(client):
    await client.post("/api/v1/events", json=_event(external_event_id="seed-1",
                                                    display_name="Ada Lovelace"))
    parked = await client.post("/api/v1/events", json=_event(external_event_id="seed-2",
                                                             display_name="Ada Rutherford"))
    review_id = parked.json()["review_id"]

    listing = await client.get("/api/v1/manual-review?status=pending")
    assert listing.status_code == 200
    ids = [item["id"] for item in listing.json()]
    assert review_id in ids
    for item in listing.json():
        assert item["status"] == "pending"

    resolved = await client.post(f"/api/v1/manual-review/{review_id}/resolve",
                                 json={"decision": "create_new"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    # After resolution it leaves the pending list.
    listing2 = await client.get("/api/v1/manual-review?status=pending")
    assert review_id not in [item["id"] for item in listing2.json()]


async def test_manual_review_resolve_merge_into(client):
    await client.post("/api/v1/events", json=_event(external_event_id="seed-1",
                                                    display_name="Ada Lovelace"))
    parked = await client.post("/api/v1/events", json=_event(external_event_id="seed-2",
                                                             display_name="Ada Rutherford"))
    review_id = parked.json()["review_id"]

    # merge_into without identity_id -> 400
    bad = await client.post(f"/api/v1/manual-review/{review_id}/resolve",
                            json={"decision": "merge_into"})
    assert bad.status_code == 400

    # 404 for an unknown review id
    import uuid
    missing = await client.post(f"/api/v1/manual-review/{uuid.uuid4()}/resolve",
                                json={"decision": "create_new"})
    assert missing.status_code == 404


async def test_manual_review_resolve_already_resolved_409(client):
    await client.post("/api/v1/events", json=_event(external_event_id="seed-1",
                                                    display_name="Ada Lovelace"))
    parked = await client.post("/api/v1/events", json=_event(external_event_id="seed-2",
                                                             display_name="Ada Rutherford"))
    review_id = parked.json()["review_id"]
    await client.post(f"/api/v1/manual-review/{review_id}/resolve",
                      json={"decision": "create_new"})
    again = await client.post(f"/api/v1/manual-review/{review_id}/resolve",
                              json={"decision": "create_new"})
    assert again.status_code == 409
