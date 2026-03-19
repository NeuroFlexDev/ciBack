# app/core/security.py
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# Use a passlib-native scheme so auth does not depend on an external bcrypt backend.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALG)
