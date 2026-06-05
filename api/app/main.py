from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Windows: psycopg async requires SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from api.app.core.config import settings
from api.app.db.base import engine


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

# ── Routers ───────────────────────────────────────────────────────────────────
from api.app.api.v1.health import router as health_router  # noqa: E402
from api.app.api.v1.auth import router as auth_router  # noqa: E402
from api.app.api.v1.tenant import router as tenant_router  # noqa: E402
from api.app.api.v1.hospital.patients import router as patients_router  # noqa: E402
from api.app.api.v1.hospital.feedback import router as feedback_router  # noqa: E402
from api.app.api.v1.hospital.outbound_dashboard import router as outbound_dash_router  # noqa: E402
from api.app.api.v1.hospital.inbound_dashboard import router as inbound_dash_router  # noqa: E402

_V1 = "/api/v1"

app.include_router(health_router)
app.include_router(auth_router, prefix=_V1)
app.include_router(tenant_router, prefix=_V1)
app.include_router(patients_router, prefix=_V1)
app.include_router(feedback_router, prefix=_V1)
app.include_router(outbound_dash_router, prefix=_V1)
app.include_router(inbound_dash_router, prefix=_V1)
