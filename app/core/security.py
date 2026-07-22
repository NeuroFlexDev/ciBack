from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from app.core.config import settings


class TokenValidationError(ValueError):
    """Raised when an access token cannot be trusted or has an invalid payload."""


class TokenExpiredError(TokenValidationError):
    """Raised when an otherwise valid access token has expired."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    ).hex()
    return f"{salt}${password_hash}"


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        salt, expected_hash = encoded_password.split("$", maxsplit=1)
    except (AttributeError, ValueError):
        return False

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    ).hex()
    return hmac.compare_digest(password_hash, expected_hash)


def create_access_token(user_id: int) -> str:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALG,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[settings.JWT_ALG],
            options={"require": ["sub", "type", "iat", "exp"]},
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Access token has expired") from exc
    except InvalidTokenError as exc:
        raise TokenValidationError("Invalid access token") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdigit():
        raise TokenValidationError("Invalid token subject")
    if payload.get("type") != "access":
        raise TokenValidationError("Invalid token type")

    return payload
