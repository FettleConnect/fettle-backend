from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from fettle_backend.platform.db.models import import_model_modules
from fettle_backend.products.transcribe.repositories.sqlalchemy import SqlDoctorRepository, SqlSessionRepository


async def seed_transcribe_defaults(session: AsyncSession) -> None:
    import_model_modules()
    await SqlDoctorRepository(session).seed_defaults()


async def reset_transcribe_runtime_state(session: AsyncSession) -> None:
    import_model_modules()
    await SqlDoctorRepository(session).reset_runtime_state()
    await SqlSessionRepository(session).reset_sessions()
