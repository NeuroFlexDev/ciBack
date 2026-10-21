from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.auth import AuthCredentials, RegistrationCredentials, AuthResponse, UserResponse
from app.services.auth_service import AuthService, get_current_user
from app.services.legal_service import require_current_version, record_receipt

router = APIRouter()


@router.post("/auth/register", response_model=AuthResponse, summary="Регистрация пользователя")
def register(payload: RegistrationCredentials, db: Session = Depends(get_db)):
    require_current_version(payload.legal_version)
    user, token = AuthService.register(db, payload.email, payload.password)
    user.full_name = payload.full_name.strip()
    record_receipt(db, email=user.email, user_id=user.id, slug="terms", context="registration")
    if payload.marketing_accepted:
        record_receipt(db, email=user.email, user_id=user.id, slug="marketing-consent", context="registration")
    # Account and all receipts are committed by get_db as one transaction.
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email, full_name=user.full_name),
    )


@router.post("/auth/login", response_model=AuthResponse, summary="Вход пользователя")
def login(payload: AuthCredentials, db: Session = Depends(get_db)):
    user, token = AuthService.authenticate(db, payload.email, payload.password)
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email, full_name=user.full_name),
    )


@router.get("/auth/me", response_model=UserResponse, summary="Текущий пользователь")
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=current_user.id, email=current_user.email, full_name=current_user.full_name)
