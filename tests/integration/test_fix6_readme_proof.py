"""Proof that Fix 6 is needed: README DATABASE_URL default was inaccurate."""
import pathlib

def test_fix6_readme_accurately_describes_database_url_default():
    readme = pathlib.Path("README.md").read_text()
    # After fix, README must state code-level default is empty string
    # and that effective default comes from docker-compose.yml
    assert 'DATABASE_URL' in readme
    # Look for the specific accurate phrasing
    assert '""' in readme or "empty" in readme.lower(), "README should mention code default is empty string"
    assert "docker-compose.yml" in readme, "README should mention docker-compose.yml as source of effective default"
    # The old inaccurate row had `postgresql+asyncpg://dsw:dsw_local_dev@db:5432/dsw` as if it were code default without qualification
    # After fix, that string should still appear but qualified as compose effective default, not bare code default
    # Ensure the row mentions both empty and compose
    lines = [l for l in readme.splitlines() if "DATABASE_URL" in l and "|" in l]
    assert len(lines) >= 1
    row = lines[0]
    # Row should contain empty string indication and compose reference or postgresql URL
    assert "empty" in row.lower() or '""' in row, f"Row should mention empty code default: {row}"
    # Also ensure code actually has empty default (not compose URL)
    from app.config import Settings
    # Inspect field default without env override: create Settings with no env vars by checking model_fields
    field = Settings.model_fields["database_url"]
    assert field.default == "", f"code default should be empty string, got {field.default!r}"

