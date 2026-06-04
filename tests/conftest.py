from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

# Windows: psycopg async requires SelectorEventLoop, not the default ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Set required env vars BEFORE any app imports ──────────────────────────────
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/fettle_test",
    ),
)

from api.app.db.base import Base  # noqa: E402 — must come after env setup
import api.app.models  # noqa: F401, E402 — registers all tables with Base.metadata
from api.app.main import app  # noqa: E402


# ── Test database engine ──────────────────────────────────────────────────────

TEST_DB_URL = os.environ["DATABASE_URL"].replace(
    "postgresql+psycopg://", "postgresql+psycopg_async://"
).replace(
    "postgresql://", "postgresql+psycopg_async://"
)


def _check_postgres_available() -> bool:
    """Returns True if PostgreSQL is reachable at the test URL."""
    try:
        import psycopg

        sync_url = TEST_DB_URL.replace("postgresql+psycopg_async://", "")
        # Parse user:password@host:port/dbname
        import re
        m = re.match(r"(\w+):([^@]*)@([^:/]+):?(\d+)?/(\w+)", sync_url)
        if not m:
            return False
        user, pw, host, port, db = m.groups()
        conn = psycopg.connect(
            host=host,
            port=int(port or 5432),
            dbname="postgres",
            user=user,
            password=pw,
            connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


_POSTGRES_AVAILABLE = _check_postgres_available()

requires_db = pytest.mark.skipif(
    not _POSTGRES_AVAILABLE,
    reason="PostgreSQL not reachable — start docker-compose.dev.yml to run integration tests",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    if not _POSTGRES_AVAILABLE:
        pytest.skip("PostgreSQL not available")
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Each test gets its own session, rolled back after completion."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def sync_client() -> TestClient:
    """Synchronous WSGI-style test client for simple endpoint tests."""
    return TestClient(app)


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTPX client for async test scenarios."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Reusable test data factories ──────────────────────────────────────────────

def make_org_id() -> uuid.UUID:
    return uuid.uuid4()


def make_user_id() -> uuid.UUID:
    return uuid.uuid4()
