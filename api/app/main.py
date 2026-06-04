from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

# Windows: psycopg async requires SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.core.config import settings
from api.app.db.base import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup — engine is created at module import; nothing extra needed here.
    # Alembic handles migrations; we do NOT call create_all() in production.
    yield
    # Shutdown — dispose the connection pool cleanly.
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Fettle multi-tenant backend — hospital & corporate health platform. "
        "All endpoints require a Bearer JWT encoding organization_id."
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

app.include_router(health_router)
