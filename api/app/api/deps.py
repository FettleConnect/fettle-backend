from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.security import TokenError, decode_token
from api.app.db.session import get_async_session
from api.app.models.auth import User

_bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """
    FastAPI dependency: validates Bearer JWT, returns the active User record.
    Raises HTTP 401 for invalid/expired tokens or unknown users.
    Never accepts hospital_id from request body — always derived from the token.
    """
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id_str = payload.get("sub")
    org_id_str = payload.get("organization_id")

    try:
        user_id = uuid.UUID(user_id_str)
        org_id = uuid.UUID(org_id_str)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    result = await session.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == org_id,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_tenant(
    current_user: User = Depends(get_current_user),
) -> uuid.UUID:
    """Returns the authenticated user's organization_id (the tenant context)."""
    return current_user.organization_id
