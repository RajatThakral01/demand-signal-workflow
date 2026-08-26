"""Proof that Fix 7 is needed: top-level provider/model must acknowledge Muse Spark usage."""
import json, pathlib

def test_fix7_top_level_acknowledges_multiple_providers():
    data = json.loads(pathlib.Path("ai-usage.json").read_text())
    # Top-level provider/model alone are insufficient after S0018–S0020 used Muse Spark
    # Fix 7 requires either arrays or a note directing to per-session breakdown.
    # We chose least-disruptive: top-level notes direct to per-session, confirmed with Rajat.
    top_notes = data.get("notes", [])
    has_note = any("Muse Spark" in n and "per-session" in n for n in top_notes)
    assert has_note, "top-level notes must mention Muse Spark and per-session breakdown (Fix 7, confirmed with Rajat)"

    # Verify per-session indeed has the switch
    sessions = data["sessions"]
    providers = {s["provider"] for s in sessions}
    models = {s["model"] for s in sessions if s.get("model")}
    assert "OpenRouter" in providers
    assert "Muse Spark" in providers
    assert any("muse-spark" in (m or "") for m in models)

def test_fix7_per_session_still_authoritative():
    data = json.loads(pathlib.Path("ai-usage.json").read_text())
    # Ensure no session was removed/altered (count should be >= prior)
    assert len(data["sessions"]) >= 20
