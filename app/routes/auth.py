from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.auth import AuthCredentials, RegistrationCredentials, AuthResponse, UserResponse, MFAChallengeResponse
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
        user=UserResponse.model_validate(user),
    )


@router.post("/auth/login", response_model=AuthResponse | MFAChallengeResponse, summary="Вход пользователя")
def login(payload: AuthCredentials, db: Session = Depends(get_db)):
    user, token = AuthService.authenticate(db, payload.email, payload.password)
    from app.services.mfa_service import authentication_result
    result=authentication_result(db,user)
    db.commit()
    return result


@router.get("/auth/me", response_model=UserResponse, summary="Текущий пользователь")
def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
