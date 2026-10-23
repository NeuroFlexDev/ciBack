import hashlib
import secrets
from typing import Literal
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict, SecretStr, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.surface import surface
from app.core.security import create_access_token, hash_password, normalize_email, verify_password
from app.database.db import get_db
from app.models.account import PasswordReset, EmailBinding
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


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=5, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=128)
    locale: Literal['ru', 'en'] = 'ru'

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        from app.schemas.auth import AuthCredentials
        return AuthCredentials.valid_email(value)


class EmailConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: SecretStr = Field(min_length=32, max_length=128)


def email_actor(db, user):
    version = user.auth_version
    current = db.query(User).filter_by(id=user.id).with_for_update().populate_existing().one()
    if not current.is_active or current.auth_version != version:
        raise HTTPException(401, 'Сеанс завершён. Войдите снова.')
    from app.services.auth_service import ensure_organization_active
    ensure_organization_active(current)
    if current.email or not current.username:
        raise HTTPException(409, 'Почта уже подключена. Для её изменения обратитесь в поддержку.')
    return current


@router.post('/me/email', status_code=202)
def request_email(payload: EmailRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user = email_actor(db, user)
    from app.services.mfa_service import authenticated_factor
    authenticated_factor(db, user, request.state.auth_claims)
    if not verify_password(payload.password.get_secret_value(), user.password_hash):
        raise HTTPException(400, 'Неверный текущий пароль')
    if db.query(User.id).filter_by(email=payload.email).first():
        raise HTTPException(409, 'Этот адрес уже используется. Укажите другую почту.')
    if not mail_service.configured():
        raise HTTPException(503, 'Письма временно недоступны. Попробуйте подключить почту позже.')
    now = datetime.utcnow()
    db.execute(update(EmailBinding).where(EmailBinding.user_id == user.id, EmailBinding.consumed_at.is_(None)).values(consumed_at=now))
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    expires = now + timedelta(minutes=30)
    db.add(EmailBinding(token_hash=digest, user_id=user.id, email=payload.email,
        auth_version=user.auth_version, expires_at=expires))
    url = settings.PUBLIC_APP_URL.rstrip('/') + '/auth#email_token=' + raw
    english = payload.locale == 'en'
    mail_service.queue_mail(db, payload.email,
        'Lernium — confirm your email' if english else 'Лерниум — подтвердите почту',
        ('Sign in to your Lernium account and open this link within 30 minutes:\n' if english else
         'Войдите в свой аккаунт Лерниума и откройте ссылку в течение 30 минут:\n') + url,
        expires_at=expires, deduplication_key='email-binding:' + digest, organization_id=user.organization_id)
    from app.services.organization_access import audit
    audit(db, user, 'account.email_requested', 'user', user.id)
    db.commit()
    return {'status': 'pending', 'expires_at': expires.isoformat()}


@router.post('/me/email/confirm')
def confirm_email(payload: EmailConfirm, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user = email_actor(db, user)
    from app.services.mfa_service import authenticated_factor, preserve_token
    authenticated_factor(db, user, request.state.auth_claims)
    digest = hashlib.sha256(payload.token.get_secret_value().encode()).hexdigest()
    now = datetime.utcnow()
    row = db.query(EmailBinding).filter_by(token_hash=digest, user_id=user.id).with_for_update().first()
    if not row or row.consumed_at or row.expires_at <= now or row.auth_version != user.auth_version:
        raise HTTPException(400, 'Ссылка недействительна или истекла. Запросите новое письмо в профиле.')
    if db.query(User.id).filter_by(email=row.email).first():
        raise HTTPException(409, 'Этот адрес уже используется. Укажите другую почту.')
    user.email = row.email
    user.auth_version += 1
    row.consumed_at = now
    from app.services.organization_access import audit
    audit(db, user, 'account.email_bound', 'user', user.id)
    try:
        db.flush()
        result = {'access_token': preserve_token(user, request.state.auth_claims), 'token_type': 'bearer',
            'user': UserResponse.model_validate(user)}
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Этот адрес уже используется. Укажите другую почту.') from None
    return result


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
    from app.services.mfa_service import authenticated_factor
    authenticated_factor(db,user,request.state.auth_claims)
    AuthService.change_password(db, user, payload.old_password, payload.new_password)
    from app.services.mfa_service import preserve_token
    result={"access_token": preserve_token(user,request.state.auth_claims), "token_type": "bearer"}
    db.commit()
    return result


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
    if user and (surface.get() != "admin" or user.is_admin):
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        db.add(PasswordReset(token_hash=token_hash, user_id=user.id, expires_at=datetime.utcnow() + timedelta(minutes=30)))
        url = (settings.ADMIN_APP_URL if user.is_admin else settings.PUBLIC_APP_URL).rstrip('/') + '/reset-password#token=' + raw
        mail_service.queue_reset(db, user.email, url, token_hash, payload.locale)
        db.commit()
    return {"message": "If the account exists, password reset instructions have been queued for delivery." if payload.locale == "en" else "Если аккаунт существует, инструкция поставлена в очередь отправки на указанный адрес."}


@router.post("/auth/password-reset/confirm")
def reset_confirm(payload: ResetConfirm, db: Session = Depends(get_db)):
    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    now = datetime.utcnow()
    row = db.query(PasswordReset).filter_by(token_hash=digest).first()
    if not row or (surface.get() == "admin" and not db.get(User, row.user_id).is_admin):
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
