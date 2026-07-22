from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.auth import AuthCredentials, AuthResponse, UserResponse
from app.services.auth_service import AuthService, get_current_user

router = APIRouter()


@router.post("/auth/register", response_model=AuthResponse, summary="Регистрация пользователя")
def register(payload: AuthCredentials, db: Session = Depends(get_db)):
    user, token = AuthService.register(db, payload.email, payload.password)
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email),
    )


@router.post("/auth/login", response_model=AuthResponse, summary="Вход пользователя")
def login(payload: AuthCredentials, db: Session = Depends(get_db)):
    user, token = AuthService.authenticate(db, payload.email, payload.password)
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email),
    )


@router.get("/auth/me", response_model=UserResponse, summary="Текущий пользователь")
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=current_user.id, email=current_user.email)
