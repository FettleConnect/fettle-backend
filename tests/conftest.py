from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# ── Set required env vars BEFORE any app imports ──────────────────────────────
def _set_test_default(name: str, value: str) -> None:
    if not os.environ.get(name):
        os.environ[name] = value


_set_test_default("JWT_SECRET_KEY", "test-secret-key-at-least-32-chars-long!!")
os.environ["DEBUG"] = "false"
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/fettle_test",
)
_set_test_default("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
_set_test_default("INTERNAL_API_BASE_URL", "http://test")
_set_test_default("REDIS_URL", "redis://localhost:6379/0")
_set_test_default("CELERY_BROKER_URL", "redis://localhost:6379/0")
_set_test_default("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
_set_test_default("LIVEKIT_URL_INTERNAL", "http://localhost:7880")
_set_test_default("LIVEKIT_URL", "")
_set_test_default("LIVEKIT_API_KEY", "")
_set_test_default("LIVEKIT_API_SECRET", "")
_set_test_default("OPENAI_API_KEY", "test-openai-key")
_set_test_default("OPENROUTER_API_KEY", "test-openrouter-key")
_set_test_default("LEGACY_AUTH_SECRET", "test-legacy-auth-secret")
_set_test_default("INTERNAL_API_ENABLED", "false")
_set_test_default("INTERNAL_API_KEY", "test-internal-key")
_set_test_default(
    "ARTIFACT_STORAGE_ROOT",
    str(Path(__file__).resolve().parents[1] / "var" / "test-artifacts"),
)
_set_test_default("PDF_DELIVERY_PUBLIC_BASE_URL", "http://localhost:8000")
_set_test_default("PDF_DELIVERY_TOKEN_SECRET", "test-pdf-delivery-secret")
_set_test_default("FETTLE_SYNC_ENABLED", "false")
_set_test_default("FETTLE_BACKEND_URL", "http://127.0.0.1:1")
_set_test_default("FETTLE_SYNC_SECRET", "test-secret")

import api.app.models  # noqa: F401, E402 — registers all tables with Base.metadata
from api.app.db.base import Base  # noqa: E402 — must come after env setup
from api.app.db.session import get_async_session  # noqa: E402
from api.app.main import app  # noqa: E402
from api.app.services.transcribe.bootstrap import (  # noqa: E402
    reset_transcribe_runtime_state,
    seed_transcribe_defaults,
)

# ── Test database engine ──────────────────────────────────────────────────────

TEST_DB_URL = (
    os.environ["DATABASE_URL"]
    .replace("postgresql+psycopg://", "postgresql+psycopg_async://")
    .replace("postgresql://", "postgresql+psycopg_async://")
)


def _sync_postgres_url(url: str) -> str:
    return url.replace("postgresql+psycopg_async://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _admin_database_url(sync_url: str) -> tuple[str, str] | None:
    parsed = urlparse(sync_url)
    database_name = parsed.path.lstrip("/")
    if not database_name:
        return None
    admin_url = urlunparse(parsed._replace(path="/postgres"))
    return admin_url, database_name


def _check_postgres_available() -> bool:
    """Ensure the configured test database exists when PostgreSQL is reachable."""
    try:
        import psycopg
        from psycopg import sql

        sync_url = _sync_postgres_url(TEST_DB_URL)
        admin_parts = _admin_database_url(sync_url)
        if admin_parts is None:
            return False
        admin_url, database_name = admin_parts

        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (database_name,),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        sql.SQL("CREATE DATABASE {}").format(
                            sql.Identifier(database_name)
                        )
                    )
        return True
    except Exception:
        return False


_POSTGRES_AVAILABLE = _check_postgres_available()

requires_db = pytest.mark.skipif(
    not _POSTGRES_AVAILABLE,
    reason=(
        "PostgreSQL not reachable - start the compose db service or set "
        "TEST_DATABASE_URL to run integration tests"
    ),
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


@pytest_asyncio.fixture(autouse=True)
async def _seed_transcribe_defaults(
    db_session: AsyncSession,
) -> AsyncGenerator[None, None]:
    await seed_transcribe_defaults(db_session)
    await reset_transcribe_runtime_state(db_session)
    yield


@pytest.fixture
def client(sync_client: TestClient) -> TestClient:
    return sync_client


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
