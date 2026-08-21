"""Shared pytest fixtures.

Forces the test DB URL onto the process **before any app module is imported**
(config/session read it at import time), so the app under test and the fixtures
run against an isolated test database — never the app's dev DATABASE_URL.

This is a hard assignment, not ``setdefault``: when tests run inside the app image
via ``docker compose run``, the container already carries ``DATABASE_URL`` pointing
at the dev ``dsw`` database; ``setdefault`` would silently keep that and let the
suite drop/create against dev data. Overwriting it pins every connection in the
pytest process to the isolated test database.

``TEST_DATABASE_URL`` defaults to a `dsw_test` database created by
``docker/init`` on the same ``db`` service (docker compose) or to a local
``dsw_test`` on the host's Postgres socket when running outside Docker.
"""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or (
    "postgresql+asyncpg://rajatthakral@/dsw_test?host=/tmp"
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# The Compose service may carry a real/local admin key. Tests exercise both the
# authorized and unauthorized branches, so pin the app imported by pytest to a
# deterministic isolated key just as we pin its database URL above.
os.environ["ADMIN_API_KEY"] = "test_admin_key"
os.environ.setdefault("APP_ENV", "test")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.models import Base
from app.db.session import get_engine, get_session_factory
from app.main import app


@pytest_asyncio.fixture
async def db_engine():
    """Fresh schema per test so tests never share state (drop + recreate)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(db_engine):
    async with get_session_factory()() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
