from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.core.config import settings
from api.app.db.base import engine
from api.app.routers.legacy_hospital import router as legacy_hospital_router
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
from api.app.transcribe.api.router import (
    router as pwa_transcribe_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await engine.dispose()


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
