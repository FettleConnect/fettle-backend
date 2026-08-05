from __future__ import annotations

import uuid
from datetime import timedelta

import pytest


class TestPasswordHashing:
    def test_hash_returns_non_empty_string(self) -> None:
        from api.app.core.security import hash_password

        h = hash_password("mysecret")
        assert isinstance(h, str)
        assert len(h) > 20

    def test_verify_correct_password(self) -> None:
        from api.app.core.security import hash_password, verify_password

        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_verify_wrong_password(self) -> None:
        from api.app.core.security import hash_password, verify_password

        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_hash_is_not_plaintext(self) -> None:
        from api.app.core.security import hash_password

        h = hash_password("plaintext")
        assert h != "plaintext"

    def test_same_password_produces_different_hashes(self) -> None:
        from api.app.core.security import hash_password

        h1 = hash_password("password")
        h2 = hash_password("password")
        assert h1 != h2  # bcrypt salts each hash


class TestJWT:
    def _make_token(self, **kwargs):
        from api.app.core.security import create_access_token

        defaults = {
            "user_id": uuid.uuid4(),
            "email": "test@hospital.com",
            "organization_id": uuid.uuid4(),
            "role": "hospital_user",
        }
        defaults.update(kwargs)
        return create_access_token(**defaults)

    def test_create_token_returns_string(self) -> None:
        token = self._make_token()
        assert isinstance(token, str)
        assert len(token) > 10

    def test_decode_valid_token(self) -> None:
        from api.app.core.security import decode_token

        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        token = self._make_token(
            user_id=user_id,
            email="doc@example.com",
            organization_id=org_id,
            role="doctor",
        )
        payload = decode_token(token)
        assert payload["sub"] == str(user_id)
        assert payload["email"] == "doc@example.com"
        assert payload["organization_id"] == str(org_id)
        assert payload["role"] == "doctor"

    def test_decode_expired_token_raises(self) -> None:
        from api.app.core.security import TokenError, create_access_token, decode_token

        token = create_access_token(
            user_id=uuid.uuid4(),
            email="x@x.com",
            organization_id=uuid.uuid4(),
            role="hr",
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(TokenError, match="expired"):
            decode_token(token)

    def test_decode_garbage_raises(self) -> None:
        from api.app.core.security import TokenError, decode_token

        with pytest.raises(TokenError):
            decode_token("not.a.token")

    def test_decode_tampered_token_raises(self) -> None:
        from api.app.core.security import TokenError, decode_token

        token = self._make_token()
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(TokenError):
            decode_token(tampered)

    def test_token_missing_claims_raises(self) -> None:
        import jwt as pyjwt

        from api.app.core.config import settings
        from api.app.core.security import TokenError, decode_token

        # Token without required claims
        bare = pyjwt.encode({"sub": "x"}, settings.jwt_secret_key, algorithm="HS256")
        with pytest.raises(TokenError, match="missing required claims"):
            decode_token(bare)
