"""Revocable refresh sessions for native clients; web bearer contract is unchanged."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from app.core.config import settings
from app.core.security import create_access_token
from app.models.integrations import MobileSession, MobileRefreshToken, MobilePushDevice
from app.schemas.auth import UserResponse
from app.services.auth_service import ensure_organization_active


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(db, user, *, amr=None, mfa_at=None, device_name="Mobile device", session=None):
    ensure_organization_active(user)
    now = datetime.utcnow()
    raw = secrets.token_urlsafe(48)
    if session is None:
        session = MobileSession(id=str(uuid.uuid4()), user_id=user.id, auth_version=user.auth_version,
                                device_name=device_name[:120], token_version=0, created_at=now,
                                expires_at=now + timedelta(minutes=settings.REFRESH_TOKEN_TTL_MINUTES))
        db.add(session)
    else:
        session.previous_refresh_token_hash = session.refresh_token_hash
        session.token_version += 1
    session.refresh_token_hash = digest(raw)
    session.auth_version = user.auth_version
    session.expires_at = now + timedelta(minutes=settings.REFRESH_TOKEN_TTL_MINUTES)
    # These mappers have no ORM relationship to order their inserts. Persist the
    # parent first, in the same transaction, before adding its refresh history.
    db.flush()
    db.add(MobileRefreshToken(token_hash=digest(raw), session_id=session.id, issued_at=now))
    return {"access_token": create_access_token(user.id, user.auth_version, amr=amr, mfa_at=mfa_at, sid=session.id, sv=session.token_version),
            "refresh_token": raw, "token_type": "bearer", "expires_in": settings.ACCESS_TOKEN_TTL_MINUTES * 60,
            "user": UserResponse.model_validate(user)}


def rotate(db, raw):
    token_hash = digest(raw)
    token_record = db.get(MobileRefreshToken, token_hash)
    row = db.query(MobileSession).filter_by(refresh_token_hash=token_hash).with_for_update().first()
    now = datetime.utcnow()
    if not row:
        reused = db.query(MobileSession).filter_by(id=token_record.session_id).with_for_update().first() if token_record else None
        if reused and not reused.revoked_at:
            reused.revoked_at = now
            db.query(MobilePushDevice).filter_by(session_id=reused.id).delete(synchronize_session=False)
            db.commit()
        raise HTTPException(401, "Недействительный refresh token")
    if row.revoked_at or row.expires_at <= now:
        db.query(MobilePushDevice).filter_by(session_id=row.id).delete(synchronize_session=False)
        raise HTTPException(401, "Мобильная сессия истекла или отозвана")
    from app.models.user import User
    user = db.get(User, row.user_id)
    if not user or not user.is_active or user.auth_version != row.auth_version:
        row.revoked_at = now
        db.query(MobilePushDevice).filter_by(session_id=row.id).delete(synchronize_session=False)
        raise HTTPException(401, "Доступ отозван")
    if not token_record or token_record.consumed_at:
        row.revoked_at = now
        db.query(MobilePushDevice).filter_by(session_id=row.id).delete(synchronize_session=False)
        db.commit()
        raise HTTPException(401, "Повторное использование refresh token: сессия отозвана")
    token_record.consumed_at = now
    from app.services.mfa_service import enabled
    if enabled(db, user):
        factor = db.get(__import__('app.models.mfa', fromlist=['UserMFA']).UserMFA, user.id)
        amr = ["pwd", "otp"]
        mfa_at = int(factor.enabled_at.timestamp()) if factor and factor.enabled_at else int(now.timestamp())
    else:
        amr, mfa_at = ["pwd"], None
    return issue(db, user, amr=amr, mfa_at=mfa_at, session=row)
