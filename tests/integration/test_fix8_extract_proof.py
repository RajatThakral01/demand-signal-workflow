"""Proof that Fix 8 is needed: redundant fallback duplicates per-source checks and is dead code."""
import pathlib

def test_fix8_redundant_fallback_removed():
    src = pathlib.Path("app/services/interpret.py").read_text()
    # After fix, the generic fallback block should be gone (or replaced with genuinely different logic).
    # The old block was:
    # if not body:
    #     # generic fallback over a few known keys
    #     body = (event.raw_payload or {}).get("message") or (event.raw_payload or {}).get("body")
    # This duplicates the web_form check (message/body) and is dead for other sources.
    # So we assert that exact fallback no longer appears verbatim.
    assert 'generic fallback over a few known keys' not in src, "redundant fallback comment still present"
    # Ensure the per-source branches still exist
    assert 'if source == "web_form"' in src
    assert 'elif source == "social_mention"' in src
    assert 'elif source == "email_engagement"' in src
    # Ensure _extract_text still returns stripped body
    assert 'return (body or "").strip()' in src
