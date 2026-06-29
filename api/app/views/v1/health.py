from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from fettle_backend.products.transcribe.api.dependencies import require_internal_admin

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "api",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": "0.1.0",
        "mockMode": True,
    }


@router.get("/internal/health", tags=["health"])
async def internal_health_check(
    actor: dict[str, str] = Depends(require_internal_admin),
) -> dict[str, object]:
    return {
        "status": "ok",
        "service": "api",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": "0.1.0",
        "internalApi": {
            "authenticated": True,
            "authenticationMethod": actor["authenticationMethod"],
            **({"caller": actor["caller"]} if actor.get("caller") else {}),
            **({"adminUserId": actor["adminUserId"]} if actor.get("adminUserId") else {}),
            **({"adminUserEmail": actor["adminUserEmail"]} if actor.get("adminUserEmail") else {}),
        },
    }
