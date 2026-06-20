from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api.app.core.config import settings
from api.app.db.base import Base

sync_engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


__all__ = ["Base", "SessionLocal", "get_session", "sync_engine"]
