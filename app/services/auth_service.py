from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import (
    TokenValidationError,
    create_access_token,
    decode_access_token,
    hash_password,
    normalize_email,
    verify_password,
)
from app.database.db import get_db
from app.models.user import User
from app.repositories.user import UserRepository
from app.core.config import settings


bearer_scheme = HTTPBearer(auto_error=False)


def _credentials_exception(detail: str = "Не удалось проверить учетные данные") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthService:
    @staticmethod
    def register(db: Session, email: str, password: str) -> tuple[User, str]:
        if not settings.REGISTRATION_ENABLED:
            raise HTTPException(403, "Регистрация закрыта. Обратитесь к администратору.")
        normalized_email = normalize_email(email)
        if UserRepository.get_by_email(db, normalized_email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Пользователь с таким email уже существует",
            )

        user = UserRepository.create(
            db,
            email=normalized_email,
            password_hash=hash_password(password),
        )
        return user, create_access_token(user.id)

    @staticmethod
    def authenticate(db: Session, email: str, password: str) -> tuple[User, str]:
        user = UserRepository.get_by_email(db, normalize_email(email))
        if not user or not user.is_active or not verify_password(password, user.password_hash):
            raise _credentials_exception("Неверный email или пароль")
        ensure_organization_active(user)
        if not user.password_hash.startswith("pbkdf2_sha256$600000$"):
            user.password_hash = hash_password(password)
            db.flush()
        return user, create_access_token(user.id, user.auth_version)

    @staticmethod
    def get_user_from_token(db: Session, token: str) -> User:
        try:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
        except (TokenValidationError, KeyError, TypeError, ValueError) as exc:
            raise _credentials_exception() from exc

        user = UserRepository.get_by_id(db, user_id)
        if user is None or not user.is_active:
            raise _credentials_exception("Пользователь не найден")
        if payload.get("ver", 0) != user.auth_version:
            raise _credentials_exception("Сеанс завершён. Войдите снова.")
        ensure_organization_active(user)
        return user

    @staticmethod
    def change_password(db: Session, user: User, old_password: str, new_password: str) -> User:
        if not verify_password(old_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный текущий пароль",
            )
        user.password_hash = hash_password(new_password)
        user.auth_version += 1
        db.flush()
        return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _credentials_exception("Требуется аутентификация")
    return AuthService.get_user_from_token(db, credentials.credentials)


def ensure_organization_active(user: User) -> None:
    if user.organization_id and user.organization and not user.organization.is_active and not user.is_admin:
        raise HTTPException(403, "Доступ организации приостановлен. Обратитесь к её администратору.")


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Доступ только для администратора Лерниума")
    return user
