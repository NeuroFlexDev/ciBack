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
        if not user or not verify_password(password, user.password_hash):
            raise _credentials_exception("Неверный email или пароль")
        return user, create_access_token(user.id)

    @staticmethod
    def get_user_from_token(db: Session, token: str) -> User:
        try:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
        except (TokenValidationError, KeyError, TypeError, ValueError) as exc:
            raise _credentials_exception() from exc

        user = UserRepository.get_by_id(db, user_id)
        if user is None:
            raise _credentials_exception("Пользователь не найден")
        return user

    @staticmethod
    def change_password(db: Session, user: User, old_password: str, new_password: str) -> User:
        if not verify_password(old_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный текущий пароль",
            )
        user.password_hash = hash_password(new_password)
        db.flush()
        return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _credentials_exception("Требуется аутентификация")
    return AuthService.get_user_from_token(db, credentials.credentials)
