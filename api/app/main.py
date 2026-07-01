from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.core.config import Settings, settings
from api.app.db.base import engine
from api.app.routers.legacy_hospital import router as legacy_hospital_router
from api.app.transcribe.api.router import router as pwa_transcribe_router
from api.app.transcribe.bootstrap import seed_transcribe_defaults
from api.app.transcribe.db import SessionFactory
from api.app.transcribe.repositories.sqlalchemy import (
    SqlSessionRepository,
    SqlSyncRepository,
)
from api.app.transcribe.services.fettle_sync import FettleSyncService
from api.app.transcribe.services.pdf_delivery import PdfDeliveryService
from api.app.transcribe.services.sessions import SessionService
from api.app.views.admin import router as admin_router
from api.app.views.admin import subdomain_router as admin_subdomain_router
from api.app.views.v1.auth import router as auth_router
from api.app.views.v1.chatbot import router as chatbot_router
from api.app.views.v1.chatbot_compat import _GOOGLE_AUTH_ROUTER as google_auth_router
from api.app.views.v1.chatbot_compat import _LEGACY_ROUTER as legacy_compat_router

# ── Routers ───────────────────────────────────────────────────────────────────
from api.app.views.v1.health import router as health_router
from api.app.views.v1.hospital.calls import router as calls_router
from api.app.views.v1.hospital.feedback import router as feedback_router
from api.app.views.v1.hospital.inbound_dashboard import router as inbound_dash_router
from api.app.views.v1.hospital.internal import router as internal_router
from api.app.views.v1.hospital.management import router as management_router
from api.app.views.v1.hospital.medivoice import router as medivoice_router
from api.app.views.v1.hospital.outbound_dashboard import router as outbound_dash_router
from api.app.views.v1.hospital.patients import router as patients_router
from api.app.views.v1.hospital.webhooks import router as webhooks_router
from api.app.views.v1.tenant import router as tenant_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    background_tasks: list[asyncio.Task] = []
    async with SessionFactory() as session:
        await seed_transcribe_defaults(session)
        await SessionService(
            SqlSessionRepository(session), settings
        ).resume_pending_report_context_jobs()
    if settings.fettle_sync_enabled:
        background_tasks.append(asyncio.create_task(_run_fettle_sync_worker(settings)))
    background_tasks.append(asyncio.create_task(_run_pdf_delivery_worker(settings)))
    yield
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with suppress(asyncio.CancelledError):
            await task
    await engine.dispose()


async def _run_fettle_sync_worker(settings: Settings) -> None:
    while True:
        try:
            async with SessionFactory() as session:
                service = FettleSyncService(SqlSyncRepository(session), settings)
                await service.process_due_jobs(limit=10)
        except Exception:
            pass
        await asyncio.sleep(max(settings.fettle_sync_retry_delay_seconds, 5.0))


async def _run_pdf_delivery_worker(settings: Settings) -> None:
    while True:
        try:
            async with SessionFactory() as session:
                service = PdfDeliveryService(SqlSessionRepository(session), settings)
                await service.process_pending_jobs(limit=10)
        except Exception:
            pass
        await asyncio.sleep(5.0)


app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description=(
        "Fettle multi-tenant backend — hospital & corporate health platform. "
        "All endpoints require a Bearer JWT encoding organization_id. "
        "hospital_id is never accepted in request body — always derived from the JWT."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_V1 = "/api/v1"

app.include_router(health_router)
app.include_router(pwa_transcribe_router)
app.include_router(admin_subdomain_router)
app.include_router(admin_router)
# Legacy-compatible routes — mounted at root to match what the frontend calls
app.include_router(google_auth_router)
app.include_router(legacy_compat_router)
app.include_router(legacy_hospital_router, prefix="/api")
# Internal API — served at root /api/ (not /api/v1/) to match legacy agent paths
app.include_router(internal_router, prefix="/api")
app.include_router(auth_router, prefix=_V1)
app.include_router(tenant_router, prefix=_V1)
app.include_router(patients_router, prefix=_V1)
app.include_router(feedback_router, prefix=_V1)
app.include_router(outbound_dash_router, prefix=_V1)
app.include_router(inbound_dash_router, prefix=_V1)
app.include_router(webhooks_router, prefix=_V1)
app.include_router(calls_router, prefix=_V1)
app.include_router(medivoice_router, prefix=_V1)
app.include_router(management_router, prefix=_V1)
app.include_router(chatbot_router, prefix=_V1)
