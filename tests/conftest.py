"""Shared pytest fixtures.

Sets the test DB URL before any app module is imported (config/session read it at
import time), then gives the FastAPI app an AsyncClient over ASGITransport backed
by that same real Postgres (integration tests run against real Postgres, not
mocks — PRD §11 and Phase-1 gate requirement).
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://rajatthakral@/dsw_test?host=/tmp"
)
os.environ.setdefault("ADMIN_API_KEY", "test_admin_key")
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