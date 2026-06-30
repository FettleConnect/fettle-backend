from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from api.app.core.config import settings

# ── Password utilities ────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT utilities ─────────────────────────────────────────────────────────────


class TokenError(Exception):
    """Raised when a JWT cannot be validated."""


def create_access_token(
    *,
    user_id: uuid.UUID,
    email: str,
    organization_id: uuid.UUID,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "organization_id": str(organization_id),
        "role": role,
        "exp": expire,
        "iat": datetime.now(tz=timezone.utc),
    }
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def create_admin_access_token(
    *,
    username: str,
    expires_delta: timedelta | None = None,
) -> str:
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    payload: dict[str, Any] = {
        "sub": "platform-admin",
        "username": username,
        "role": "superadmin",
        "scope": "platform_admin",
        "exp": expire,
        "iat": datetime.now(tz=timezone.utc),
    }
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises TokenError on any failure."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc

    required = {"sub", "email", "organization_id", "role"}
    if not required.issubset(payload):
        raise TokenError("Token missing required claims")

    return payload


def decode_admin_token(token: str) -> dict[str, Any]:
    """Decode and validate a platform admin JWT."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc

    if payload.get("scope") != "platform_admin" or payload.get("role") != "superadmin":
        raise TokenError("Token is not a platform admin token")

    return payload
