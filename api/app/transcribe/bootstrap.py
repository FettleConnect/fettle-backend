from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.transcribe.models import import_model_modules
from api.app.transcribe.repositories.sqlalchemy import (
    SqlDoctorRepository,
    SqlSessionRepository,
)


async def seed_transcribe_defaults(session: AsyncSession) -> None:
    import_model_modules()
    await SqlDoctorRepository(session).seed_defaults()


async def reset_transcribe_runtime_state(session: AsyncSession) -> None:
    import_model_modules()
    await SqlDoctorRepository(session).reset_runtime_state()
    await SqlSessionRepository(session).reset_sessions()
