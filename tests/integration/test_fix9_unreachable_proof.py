"""Proof that Fix 9 is needed: unreachable check should be removed."""
import pathlib

def test_fix9_unreachable_check_removed():
    src = pathlib.Path("app/routers/admin.py").read_text()
    # Old code:
    # if outcome["interpretation"] is None:
    #     raise HTTPException(status_code=409, detail={"error": "replay_failed"})
    # Per code trace, classify_event (via run_downstream) always either returns
    # with an interpretation row or raises InterpretError, so this branch is dead.
    assert 'outcome["interpretation"] is None' not in src, "unreachable check still present"
    # Ensure run_downstream and classify_event still exist and are used
    assert "run_downstream" in src
    assert "InterpretError" in src
