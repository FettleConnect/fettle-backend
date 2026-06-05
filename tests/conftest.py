from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fettle_backend.app.main import create_app
from fettle_backend.platform.db.session import SessionFactory
from fettle_backend.products.transcribe.bootstrap import reset_transcribe_runtime_state, seed_transcribe_defaults


def pytest_configure() -> None:
    # Keep asyncio plugin available without forcing every test to be async.
    pass


@pytest.fixture
def client() -> Iterator[TestClient]:
    subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        check=True,
        cwd="/home/snehit/fettle/fettle-backend",
    )

    async def _prepare() -> None:
        async with SessionFactory() as session:
            await seed_transcribe_defaults(session)
            await reset_transcribe_runtime_state(session)

    asyncio.run(_prepare())

    with TestClient(create_app()) as test_client:
        yield test_client
