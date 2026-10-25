from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import get_db
from app.models.integrations import MobileSession, MobilePushDevice
from app.models.integrations import MobilePushPreference
from app.services.auth_service import AuthService, get_current_user
from app.services.mobile_sessions import issue, rotate
from app.services.mfa_service import authentication_result
from app.schemas.mobile_contract import (MobileBootstrapResponse, MobileMFAChallengeResponse,
    MobileSessionResponse, OkResponse, MobileDataPolicyResponse, PushDeviceInput, PushDeviceResponse)
from app.services.mobile_sessions import digest
from app.services.integration_secrets import cipher, seal
from app.schemas.mobile_contract import MobilePushPreferences
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid

router = APIRouter()

MOBILE_FEATURES = ("material_downloads", "offline_access", "backups")


def mobile_feature_map(db, organization_id):
    """Return only explicitly supported, currently enabled mobile features."""
    from app.services.learning_service import settings_for
    if not organization_id:
        return {}
    delivery = settings_for(db, organization_id).delivery
    policy = delivery.get("mobile_data_policy", {}) if isinstance(delivery, dict) else {}
    if not isinstance(policy, dict):
        return {}
    return {name: policy[name] is True for name in MOBILE_FEATURES if policy.get(name) is True}


def require_mobile_feature(db, organization_id, feature):
    if feature not in MOBILE_FEATURES or feature not in mobile_feature_map(db, organization_id):
        raise HTTPException(403, "Mobile feature is disabled for this organization")


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    device_name: str = Field(default="Mobile device", min_length=1, max_length=120)


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=32, max_length=256)


@router.get("/auth/mobile/policy", response_model=MobileDataPolicyResponse)
def data_policy(db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.learning_service import settings_for
    delivery = settings_for(db, user.organization_id).delivery if user.organization_id else {}
    policy = delivery.get("mobile_data_policy", {}) if isinstance(delivery, dict) else {}
    enabled = mobile_feature_map(db, user.organization_id)
    return {"material_downloads": "material_downloads" in enabled,
            "offline_access": "offline_access" in enabled,
            "cache_ttl_hours": policy.get("cache_ttl_hours", 24),
            "backups": "backups" in enabled,
            "features": enabled}


@router.put("/auth/mobile/push-device", response_model=PushDeviceResponse)
def register_push_device(payload: PushDeviceInput, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    sid = request.state.auth_claims.get("sid")
    if not sid:
        raise HTTPException(400, "Mobile session required")
    if len(payload.token) > 4096 or payload.platform not in {"ios", "android"}:
        raise HTTPException(422, "Invalid push device")
    try:
        cipher()
    except Exception:
        raise HTTPException(503, "Push token storage is not configured")
    row = db.query(MobilePushDevice).filter_by(session_id=sid).first()
    token_hash = digest(payload.token)
    # A native token identifies one installation. Reassign it atomically when a
    # different account signs in on that installation.
    db.query(MobilePushDevice).filter(MobilePushDevice.token_hash == token_hash,
                                      MobilePushDevice.session_id != sid).delete(synchronize_session=False)
    if row is None:
        row = MobilePushDevice(id=str(uuid.uuid4()), session_id=sid,
                               token_hash=token_hash, token_encrypted=seal(payload.token), platform=payload.platform)
        db.add(row)
    else:
        row.token_hash, row.token_encrypted, row.platform = token_hash, seal(payload.token), payload.platform
    db.commit()
    return {"registered": True}


@router.delete("/auth/mobile/push-device", response_model=OkResponse)
def revoke_push_device(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    sid = request.state.auth_claims.get("sid")
    if sid:
        db.query(MobilePushDevice).filter_by(session_id=sid).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.get("/auth/mobile/push-preferences", response_model=MobilePushPreferences)
def get_push_preferences(db: Session = Depends(get_db), user=Depends(get_current_user)):
    row = db.get(MobilePushPreference, user.id)
    if not row:
        return MobilePushPreferences()
    return MobilePushPreferences.model_validate(row, from_attributes=True)


@router.put("/auth/mobile/push-preferences", response_model=MobilePushPreferences)
def update_push_preferences(payload: MobilePushPreferences, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "timezone must be a valid IANA timezone")
    row = db.get(MobilePushPreference, user.id)
    if row is None:
        row = MobilePushPreference(user_id=user.id)
        db.add(row)
    row.assignment_enabled = payload.assignment_enabled
    row.deadline_enabled = payload.deadline_enabled
    row.result_enabled = payload.result_enabled
    row.timezone = payload.timezone
    db.commit()
    return payload


@router.get("/auth/mobile/bootstrap", response_model=MobileBootstrapResponse)
def bootstrap():
    methods = list(settings.MOBILE_LOGIN_METHODS)
    return {"api_origin": settings.PUBLIC_APP_URL.rstrip("/"),
            "display_domain": settings.MOBILE_TENANT_DISPLAY_DOMAIN or settings.PUBLIC_APP_URL.split("//")[-1].split("/")[0],
            "login_methods": methods, "oidc_available": bool(settings.MOBILE_OIDC_REDIRECT_URI),
            "api_compatibility": settings.MOBILE_API_COMPATIBILITY,
            "minimum_app_version": settings.MOBILE_APP_MIN_VERSION,
            "recommended_app_version": settings.MOBILE_APP_RECOMMENDED_VERSION,
            "app_store_url_ios": settings.MOBILE_APP_STORE_URL_IOS,
            "app_store_url_android": settings.MOBILE_APP_STORE_URL_ANDROID,
            "push_notifications": __import__("app.services.mobile_push", fromlist=["configured"]).configured()}


@router.post("/auth/mobile/login", response_model=MobileSessionResponse | MobileMFAChallengeResponse,
             response_model_exclude_unset=True)
def login(payload: LoginInput, db: Session = Depends(get_db)):
    user, _ = AuthService.authenticate(db, payload.email, payload.password)
    result = authentication_result(db, user, primary="pwd", context={"mobile": True, "device_name": payload.device_name})
    if result.get("mfa_required"):
        db.commit()
        return result
    result = issue(db, user, amr=["pwd"])
    db.commit()
    return result


@router.post("/auth/mobile/refresh", response_model=MobileSessionResponse)
def refresh(payload: RefreshInput, db: Session = Depends(get_db)):
    result = rotate(db, payload.refresh_token)
    db.commit()
    return result


@router.post("/auth/mobile/logout", response_model=OkResponse)
def logout(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    claims = request.state.auth_claims
    sid = claims.get("sid")
    if not sid:
        raise HTTPException(400, "Требуется мобильная сессия")
    row = db.get(MobileSession, sid)
    if row and row.user_id == user.id and not row.revoked_at:
        row.revoked_at = datetime.utcnow()
        db.query(MobilePushDevice).filter_by(session_id=row.id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.post("/auth/mobile/logout-all", response_model=OkResponse)
def logout_all(db: Session = Depends(get_db), user=Depends(get_current_user)):
    ids = [row.id for row in db.query(MobileSession.id).filter(MobileSession.user_id == user.id)]
    db.query(MobileSession).filter(MobileSession.id.in_(ids)).update({"revoked_at": datetime.utcnow()}, synchronize_session=False)
    if ids:
        db.query(MobilePushDevice).filter(MobilePushDevice.session_id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}
