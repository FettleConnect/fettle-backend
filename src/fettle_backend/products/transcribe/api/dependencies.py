from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from fettle_backend.app.config import Settings, get_settings
from fettle_backend.platform.db.session import get_db_session
from fettle_backend.platform.observability.service import ObservabilityService
from fettle_backend.products.transcribe.repositories.sqlalchemy import (
    SqlDoctorRepository,
    SqlQueueRepository,
    SqlSessionRepository,
    SqlSyncRepository,
)
from fettle_backend.products.transcribe.schemas.common import ErrorResponse
from fettle_backend.products.transcribe.services.auth import AuthService
from fettle_backend.products.transcribe.services.fettle_sync import FettleSyncService
from fettle_backend.products.transcribe.services.pdf_delivery import PdfDeliveryService
from fettle_backend.products.transcribe.services.queue import QueueService
from fettle_backend.products.transcribe.services.sessions import SessionService


def get_auth_service(
    db_session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(SqlDoctorRepository(db_session), settings)


def get_session_service(
    db_session: AsyncSession = Depends(get_db_session),
) -> SessionService:
    return SessionService(SqlSessionRepository(db_session))


def get_queue_service(
    db_session: AsyncSession = Depends(get_db_session),
) -> QueueService:
    return QueueService(SqlQueueRepository(db_session), SqlSessionRepository(db_session))


def get_observability_service(
    db_session: AsyncSession = Depends(get_db_session),
) -> ObservabilityService:
    return ObservabilityService(db_session)


def get_pdf_delivery_service(
    db_session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PdfDeliveryService:
    return PdfDeliveryService(SqlSessionRepository(db_session), settings)


def get_fettle_sync_service(
    db_session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> FettleSyncService:
    return FettleSyncService(SqlSyncRepository(db_session), settings)


async def require_doctor(
    authorization: str | None = Header(default=None),
    auth_service: AuthService = Depends(get_auth_service),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=ErrorResponse(message="Authentication required.").model_dump())
    token = authorization.removeprefix("Bearer ").strip()
    return (await auth_service.current_doctor(token)).doctor


def require_internal_admin(
    authorization: str | None = Header(default=None),
    x_internal_api_key: str | None = Header(default=None),
    x_internal_caller: str | None = Header(default=None),
    x_admin_user_id: str | None = Header(default=None),
    x_admin_user_email: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization.removeprefix("Bearer ").removeprefix("bearer ").strip()
    credential = x_internal_api_key or bearer_token
    if not settings.internal_api_enabled or not credential or credential != settings.internal_api_key:
        raise HTTPException(status_code=401, detail=ErrorResponse(message="Internal admin authentication required.").model_dump())
    actor = {
        "authenticationMethod": "x-internal-api-key" if x_internal_api_key else "authorization",
        "caller": x_internal_caller or "internal-admin",
    }
    if x_admin_user_id:
        actor["adminUserId"] = x_admin_user_id
    if x_admin_user_email:
        actor["adminUserEmail"] = x_admin_user_email
    return actor
