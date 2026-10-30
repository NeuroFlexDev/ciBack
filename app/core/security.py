from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from app.core.config import settings
from app.core.surface import surface


def signing_key():
    return (settings.ADMIN_JWT_SECRET if surface.get() == "admin" else settings.JWT_SECRET).get_secret_value()


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
        600000,
    ).hex()
    return f"pbkdf2_sha256$600000${salt}${password_hash}"


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        parts = encoded_password.split("$")
        if len(parts) == 2:
            salt, expected_hash = parts
            iterations = 120000
        else:
            algorithm, work, salt, expected_hash = parts
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(work)
            if not 100000 <= iterations <= 2000000:
                return False
    except (AttributeError, ValueError, TypeError):
        return False

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(password_hash, expected_hash)


def create_access_token(user_id: int, version: int = 0, *, amr: list[str] | None = None, mfa_at: int | None = None, sid: str | None = None, sv: int | None = None, wsid: str | None = None, web_started_at: int | None = None) -> str:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "aud": "lernium:" + surface.get(),
        "ver": version,
        "iat": issued_at,
        "exp": expires_at,
        "amr": amr or [],
    }
    if mfa_at is not None:
        payload['mfa_at'] = mfa_at
    if sid is not None:
        payload['sid'] = sid
        payload['sv'] = sv
    else:
        # Rotations retain the original login boundary and never refresh MFA proof.
        started = int(issued_at.timestamp()) if web_started_at is None else web_started_at
        payload['wsid'] = wsid or secrets.token_urlsafe(24)
        payload['wsa'] = started
        payload['exp'] = min(expires_at, datetime.fromtimestamp(started, timezone.utc) + timedelta(hours=8))
    return jwt.encode(
        payload,
        signing_key(),
        algorithm=settings.JWT_ALG,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            signing_key(),
            algorithms=[settings.JWT_ALG],
            options={"require": ["sub", "type", "iat", "exp"], "verify_aud": False},
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Access token has expired") from exc
    except InvalidTokenError as exc:
        raise TokenValidationError("Invalid access token") from exc

    audience = payload.get("aud")
    # Legacy platform sessions remain valid until their normal expiry.
    if audience != "lernium:" + surface.get() and not (audience is None and surface.get() == "platform"):
        raise TokenValidationError("Token belongs to another service")
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdigit():
        raise TokenValidationError("Invalid token subject")
    if payload.get("type") != "access":
        raise TokenValidationError("Invalid token type")

    return payload
