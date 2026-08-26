"""Proof that Fix 5 is needed: requires field self-contradicts."""
from app.services.resolve import get_identity_policy

def test_fix5_requires_only_name_company_optional():
    policy = get_identity_policy()
    fuzzy = policy["rules"]["fuzzy_name_company"]
    # After fix, only name is required; company is optional
    assert fuzzy["requires"] == ["name"], f"requires should be ['name'], got {fuzzy['requires']}"
    assert "optional" in fuzzy and "company" in fuzzy["optional"], "company should be listed as optional"
    # Ensure code still doesn't read requires programmatically (purely descriptive)
    # This is the confirmation the prompt asks for: grep shows no code reads it
    import pathlib
    resolve_code = pathlib.Path("app/services/resolve.py").read_text()
    # The string '\"requires\"' should not appear in code (only in JSON)
    assert '"requires"' not in resolve_code and "'requires'" not in resolve_code

