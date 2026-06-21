from __future__ import annotations

import datetime
import os

import jwt
from passlib.hash import django_pbkdf2_sha256


def create_token(payload: dict, timeout_minutes: int = 120) -> str:
    payload = dict(payload)
    payload["exp"] = datetime.datetime.utcnow() + datetime.timedelta(
        minutes=timeout_minutes
    )
    secret = os.getenv("SECRET_KEY", "dev-secret")
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token


def verify_django_password(plain: str, hashed: str) -> bool:
    """Verify a Django-style PBKDF2+SHA256 hash using passlib helper."""
    try:
        return django_pbkdf2_sha256.verify(plain, hashed)
    except Exception:
        return False


def token_payload(token: str) -> dict:
    secret = os.getenv("SECRET_KEY", "dev-secret")
    return jwt.decode(token, secret, algorithms=["HS256"])
