import hashlib
import secrets
from typing import Literal
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password, normalize_email
from app.database.db import get_db
from app.models.account import PasswordReset
from app.models.user import User
from app.schemas.auth import UserResponse
from app.services.auth_service import AuthService, get_current_user
from app.services import mail_service

router = APIRouter()


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=1, max_length=160)


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class ResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    locale: Literal["ru", "en"] = "ru"


class ResetConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@router.patch("/me", response_model=UserResponse)
def profile(payload: ProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = payload.full_name.strip()
    if not name:
        raise HTTPException(422, "Укажите имя")
    user.full_name = name
    db.commit()
    return UserResponse.model_validate(user)


@router.post("/change-password")
def password(payload: PasswordChange, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    AuthService.change_password(db, user, payload.old_password, payload.new_password)
    db.commit()
    from app.services.mfa_service import preserve_token
    return {"access_token": preserve_token(user,request.state.auth_claims), "token_type": "bearer"}


@router.post("/auth/logout-all")
def logout_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.execute(update(User).where(User.id == user.id).values(auth_version=User.auth_version + 1))
    db.commit()
    return {"ok": True}


@router.post("/auth/password-reset")
def reset_request(payload: ResetRequest, db: Session = Depends(get_db)):
    if not mail_service.configured():
        raise HTTPException(503, "Восстановление по почте недоступно. Обратитесь к администратору.")
    user = db.query(User).filter(User.email == normalize_email(payload.email), User.is_active.is_(True)).first()
    if user:
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        db.add(PasswordReset(token_hash=token_hash, user_id=user.id, expires_at=datetime.utcnow() + timedelta(minutes=30)))
        url = settings.PUBLIC_APP_URL.rstrip('/') + '/reset-password#token=' + raw
        mail_service.queue_reset(db, user.email, url, token_hash, payload.locale)
        db.commit()
    return {"message": "If the account exists, password reset instructions have been queued for delivery." if payload.locale == "en" else "Если аккаунт существует, инструкция поставлена в очередь отправки на указанный адрес."}


@router.post("/auth/password-reset/confirm")
def reset_confirm(payload: ResetConfirm, db: Session = Depends(get_db)):
    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    now = datetime.utcnow()
    row = db.query(PasswordReset).filter_by(token_hash=digest).first()
    if not row:
        raise HTTPException(400, "Ссылка недействительна или истекла")
    claimed = db.execute(update(PasswordReset).where(PasswordReset.token_hash == digest, PasswordReset.consumed_at.is_(None), PasswordReset.expires_at > now).values(consumed_at=now))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "Ссылка недействительна или истекла")
    changed = db.execute(update(User).where(User.id == row.user_id, User.is_active.is_(True)).values(password_hash=hash_password(payload.new_password), auth_version=User.auth_version + 1))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "Ссылка недействительна или истекла")
    db.execute(update(PasswordReset).where(PasswordReset.user_id == row.user_id, PasswordReset.consumed_at.is_(None)).values(consumed_at=now))
    db.commit()
    return {"message": "Пароль обновлён. Войдите с новым паролем."}
