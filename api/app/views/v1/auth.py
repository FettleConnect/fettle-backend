from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.security import create_access_token, verify_password
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.schemas.auth import (
    LoginRequest,
    TabAccessResponse,
    TokenResponse,
    ValidateTokenResponse,
)
from api.app.views.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TokenResponse:
    result = await session.execute(
        select(User).where(
            User.email == body.email,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        role=user.role,
    )

    permissions = (
        _extract_permissions(user) if user.role in ("hospital_user", "doctor") else None
    )

    return TokenResponse(
        token=token,
        role=user.role,
        name=user.name,
        organization_id=str(user.organization_id),
        user_id=str(user.id),
        permissions=permissions,
    )


@router.get("/validate", response_model=ValidateTokenResponse)
async def validate_token(
    current_user: User = Depends(get_current_user),
) -> ValidateTokenResponse:
    return ValidateTokenResponse(
        role=current_user.role,
        name=current_user.name,
        email=current_user.email,
        user_id=str(current_user.id),
        organization_id=str(current_user.organization_id),
    )


@router.get("/tab-access", response_model=TabAccessResponse)
async def tab_access(
    current_user: User = Depends(get_current_user),
) -> TabAccessResponse:
    return TabAccessResponse(permissions=_extract_permissions(current_user))


def _extract_permissions(user: User) -> dict[str, bool]:
    return {
        "patient_engagement": bool(user.perm_patient_engagement),
        "community_engagement": bool(user.perm_community_engagement),
        "revisit_engagement": bool(user.perm_revisit_engagement),
        "escalation_engagement": bool(user.perm_escalation_engagement),
        "calllog_engagement": bool(user.perm_calllog_engagement),
        "upload_engagement": bool(user.perm_upload_engagement),
        "pdf_engagement": bool(user.perm_pdf_engagement),
    }
