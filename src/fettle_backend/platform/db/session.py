from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fettle_backend.app.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, future=True, poolclass=pool.NullPool)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
