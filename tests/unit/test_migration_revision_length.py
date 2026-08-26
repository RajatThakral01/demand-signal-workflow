"""Regression: every Alembic revision id must fit in alembic_version varchar(32).

Alembic's alembic_version table stores the current revision in a varchar(32)
(not configured by this project). A revision string longer than 32 chars will
crash `alembic upgrade head` on a fresh `docker compose up` with
asyncpg.exceptions.StringDataRightTruncationError, even though the test suite
(beyond this file) builds schema via Base.metadata.create_all and never
exercises alembic_version's column constraints — which is why the 0013 bug
(34 chars) was never caught by the existing 239 tests.

This test fails against the buggy 0013 string and passes after the rename to
0013_dlq_unresolved_unique (26 chars).
"""

import pathlib
import re

_VERSIONS = pathlib.Path("app/db/migrations/versions")


def test_all_migration_revision_ids_fit_varchar_32():
    files = sorted(_VERSIONS.glob("*.py"))
    assert files, "no migration files found"
    failures = []
    for p in files:
        text = p.read_text()
        rev_m = re.search(r'revision\s*=\s*"([^"]*)"', text)
        down_m = re.search(r'down_revision\s*=\s*(?:"([^"]*)"|None)', text)
        assert rev_m, f"{p.name} missing revision"
        rev = rev_m.group(1)
        down = down_m.group(1) if down_m and down_m.group(1) is not None else None
        if len(rev) > 32:
            failures.append(f"{p.name}: revision '{rev}' len={len(rev)} > 32")
        if down is not None and len(down) > 32:
            failures.append(f"{p.name}: down_revision '{down}' len={len(down)} > 32")
    assert not failures, "revision id(s) exceed varchar(32):\n" + "\n".join(failures)


def test_0013_revision_is_short_and_tied_to_0013():
    text = (_VERSIONS / "0013_dlq_unresolved_unique.py").read_text()
    m = re.search(r'revision\s*=\s*"([^"]*)"', text)
    assert m
    rev = m.group(1)
    assert rev == "0013_dlq_unresolved_unique"
    assert len(rev) == 26
    assert len(rev) <= 32
    assert rev.startswith("0013_")
