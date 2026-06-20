from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

# Windows: psycopg async requires SelectorEventLoop, not the default ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

# ── Set required env vars BEFORE any app imports ──────────────────────────────
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/fettle_test",
    ),
)
os.environ.setdefault("INTERNAL_API_SHARED_SECRET", "test-internal-secret")

from unittest.mock import MagicMock, patch

import api.app.models  # noqa: F401, E402 — registers all tables with Base.metadata
from api.app.db.base import Base  # noqa: E402 — must come after env setup
from api.app.db.session import get_async_session  # noqa: E402
from api.app.main import app  # noqa: E402

# ── Test database engine ──────────────────────────────────────────────────────

TEST_DB_URL = (
    os.environ["DATABASE_URL"]
    .replace("postgresql+psycopg://", "postgresql+psycopg_async://")
    .replace("postgresql://", "postgresql+psycopg_async://")
)


def _check_postgres_available() -> bool:
    """Returns True if PostgreSQL is reachable at the test URL."""
    try:
        import re

        import psycopg

        sync_url = TEST_DB_URL.replace("postgresql+psycopg_async://", "")
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
    """
    Each test gets its own session.
    Data flushed but not committed is visible within this session and the app
    (because async_client overrides get_async_session to use the same session).
    Rolled back after the test to keep the DB clean.
    """
    async_session_maker = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    session = async_session_maker()
    yield session
    await session.rollback()
    await session.close()


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTPX client whose FastAPI app shares the test session.
    This ensures data seeded via db_session is visible to the app during the test.
    """

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session
        # Flush after each request so subsequent requests see pending adds
        # (mirrors the production commit, but stays within the test's rollback)
        await db_session.flush()

    app.dependency_overrides[get_async_session] = _override_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.pop(get_async_session, None)


@pytest.fixture
def sync_client() -> TestClient:
    """Synchronous WSGI-style test client for simple endpoint tests (no DB seed needed)."""
    return TestClient(app)


# ── Reusable test data factories ──────────────────────────────────────────────


def make_org_id() -> uuid.UUID:
    return uuid.uuid4()


def make_user_id() -> uuid.UUID:
    return uuid.uuid4()


# ── Celery task mock — prevents broker connection attempts in all tests ────────


@pytest.fixture(autouse=True)
def _mock_celery_dispatch():
    """
    Prevent Celery tasks from actually dispatching to the Redis broker during tests.
    All task.delay() / apply_async() calls return a mock.
    """
    _task = MagicMock()
    _task.id = "test-task-id-fixture"

    _targets = [
        "api.app.worker.tasks.run_campaign_task.delay",
        "api.app.worker.tasks.call_outbound_task.delay",
        "api.app.worker.tasks.process_outbound_calls.delay",
        "api.app.worker.tasks.process_inbound_calls.delay",
        "api.app.worker.tasks.send_prescription_notifications.delay",
        "api.app.worker.tasks.reminder_task.delay",
        "api.app.worker.tasks.schedule_reminder_calls.delay",
        "api.app.worker.tasks.reminder_task.apply_async",
    ]
    managers = [patch(t, return_value=_task) for t in _targets]
    for m in managers:
        m.start()
    yield
    for m in managers:
        m.stop()
