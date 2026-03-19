# app/services/auth.py
from fastapi import Depends
from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import get_db
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import PasswordChange, UserCreate, UserUpdate
from app.services.security import create_access_token, hash_password, verify_password

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class AuthService:
    @staticmethod
    def register(db: Session, user_in: UserCreate) -> User:
        if UserRepository.get_by_email(db, user_in.email):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
        hashed_pw = hash_password(user_in.password)
        return UserRepository.create(db, user_in, hashed_pw)

    @staticmethod
    def authenticate(db: Session, email: str, password: str) -> User:
        user = UserRepository.get_by_email(db, email)
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    @staticmethod
    def create_token(user_id: int) -> str:
        return create_access_token({"sub": str(user_id)})

    @staticmethod
    def get_current_user(db: Session, token: str) -> User:
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        try:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
            user_id_raw = payload.get("sub")
            if user_id_raw is None:
                raise credentials_exception
            user_id = int(user_id_raw)
        except JWTError:
            raise credentials_exception
        except (TypeError, ValueError):
            raise credentials_exception
        user = UserRepository.get_by_id(db, user_id)
        if user is None:
            raise credentials_exception
        return user

    @staticmethod
    def update_user(db: Session, user: User, payload: UserUpdate) -> User:
        if payload.email is not None and payload.email != user.email:
            existing = UserRepository.get_by_email(db, payload.email)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered",
                )
        return UserRepository.update(db, user, payload)

    @staticmethod
    def change_password(db: Session, user: User, password_change: PasswordChange) -> User:
        if not verify_password(password_change.old_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wrong old password",
            )
        user.hashed_password = hash_password(password_change.new_password)
        db.commit()
        db.refresh(user)
        return user


def get_current_user_dep(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    return AuthService.get_current_user(db, token)
