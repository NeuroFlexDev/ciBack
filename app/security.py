import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User

TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 7)))
AUTH_SECRET = os.getenv("AUTH_SECRET", "lernium-dev-secret-change-me")
bearer_scheme = HTTPBearer(auto_error=False)


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
    except ValueError:
        return False

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    ).hex()
    return hmac.compare_digest(password_hash, expected_hash)


def create_access_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "email": user.email,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def decode_access_token(token: str) -> dict[str, object]:
    try:
        payload_b64, signature = token.split(".", maxsplit=1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный токен доступа",
        ) from exc

    expected_signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректная подпись токена",
        )

    padding = "=" * (-len(payload_b64) % 4)
    payload_raw = base64.urlsafe_b64decode(f"{payload_b64}{padding}".encode("utf-8"))
    payload = json.loads(payload_raw.decode("utf-8"))

    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Срок действия токена истек",
        )

    return payload


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется аутентификация",
        )

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not isinstance(user_id, int):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный токен пользователя",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден",
        )

    return user
