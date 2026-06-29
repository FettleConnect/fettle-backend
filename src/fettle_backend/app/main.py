from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI

from fettle_backend.app.config import get_settings
from fettle_backend.platform.db.session import SessionFactory
from fettle_backend.products.transcribe.api.dependencies import require_internal_admin
from fettle_backend.products.transcribe.api.router import router as transcribe_router
from fettle_backend.products.transcribe.bootstrap import seed_transcribe_defaults


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with SessionFactory() as session:
        await seed_transcribe_defaults(session)
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Fettle Backend",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
    )

    def health_payload() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "api",
            "timestamp": datetime.now(UTC).isoformat(),
            "version": app.version,
        }

    @app.get("/health", tags=["health"])
    async def healthcheck() -> dict[str, object]:
        return {**health_payload(), "mockMode": True}

    @app.get("/internal/health", tags=["health"])
    async def internal_healthcheck(actor: dict[str, str] = Depends(require_internal_admin)) -> dict[str, object]:
        return {
            **health_payload(),
            "internalApi": {
                "authenticated": True,
                "authenticationMethod": actor["authenticationMethod"],
                **({"caller": actor["caller"]} if actor.get("caller") else {}),
                **({"adminUserId": actor["adminUserId"]} if actor.get("adminUserId") else {}),
                **({"adminUserEmail": actor["adminUserEmail"]} if actor.get("adminUserEmail") else {}),
            },
        }

    app.include_router(transcribe_router)
    return app


app = create_app()
