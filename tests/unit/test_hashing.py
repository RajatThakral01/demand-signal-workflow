"""Unit tests — deterministic hashing for dedupe/edit detection (FR-2)."""

from app.services.ingest import (
    canonical_json,
    compute_dedupe_key,
    compute_payload_hash,
)


def test_dedupe_key_is_deterministic():
    a = compute_dedupe_key("web_form", "evt-1")
    b = compute_dedupe_key("web_form", "evt-1")
    assert a == b
    assert len(a) == 64


def test_dedupe_key_distinguishes_source_and_event():
    assert compute_dedupe_key("web_form", "evt-1") != compute_dedupe_key("social", "evt-1")
    assert compute_dedupe_key("web_form", "evt-1") != compute_dedupe_key("web_form", "evt-2")


def test_dedupe_key_none_when_inputs_missing():
    assert compute_dedupe_key(None, "evt-1") is None
    assert compute_dedupe_key("web_form", None) is None


def test_payload_hash_ignores_field_order():
    payload_a = {"b": 2, "a": {"d": 4, "c": 3}}
    payload_b = {"a": {"c": 3, "d": 4}, "b": 2}
    assert compute_payload_hash(payload_a) == compute_payload_hash(payload_b)


def test_payload_hash_changes_with_content():
    assert compute_payload_hash({"a": 1}) != compute_payload_hash({"a": 2})


def test_canonical_json_is_deterministic():
    assert canonical_json({"b": 1, "a": {"y": 2, "x": 1}}) == (
        '{"a":{"x":1,"y":2},"b":1}'
    )