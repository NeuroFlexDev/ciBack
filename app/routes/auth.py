from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    normalize_email,
    verify_password,
)

router = APIRouter()


class AuthCredentials(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    email: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


@router.post("/auth/register", response_model=AuthResponse, summary="Регистрация пользователя")
def register(payload: AuthCredentials, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()

    token = create_access_token(user)
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email),
    )


@router.post("/auth/login", response_model=AuthResponse, summary="Вход пользователя")
def login(payload: AuthCredentials, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    token = create_access_token(user)
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email),
    )


@router.get("/auth/me", response_model=UserResponse, summary="Текущий пользователь")
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=current_user.id, email=current_user.email)
