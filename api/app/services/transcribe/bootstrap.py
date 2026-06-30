from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models.hospital import transcribe as _transcribe_models  # noqa: F401
from api.app.services.transcribe.persistence import (
    SqlDoctorRepository,
    SqlSessionRepository,
)


async def seed_transcribe_defaults(session: AsyncSession) -> None:
    await SqlDoctorRepository(session).seed_defaults()


async def reset_transcribe_runtime_state(session: AsyncSession) -> None:
    await SqlDoctorRepository(session).reset_runtime_state()
    await SqlSessionRepository(session).reset_sessions()
