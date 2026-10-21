import hashlib
from dataclasses import dataclass
from datetime import datetime
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.administration import Organization
from app.models.integrations import IntegrationKey
from app.services.auth_service import get_current_user
from app.services.organization_access import audit

SCOPES = {"courses:read", "members:read", "assignments:read", "assignments:write", "results:read"}


def integration_enabled(db, organization_id):
    from app.models.learning import OrganizationLearningSettings
    row = db.get(OrganizationLearningSettings, organization_id)
    return row is None or (row.features or {}).get("integrations", True) is not False


def ensure_integrations_enabled(db, organization_id):
    if not integration_enabled(db, organization_id):
        raise HTTPException(403, "Интеграции отключены в конфигурации вашей организации")


def integration_owner(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.learning_service import is_manager
    if not user.organization_id or not is_manager(db, user):
        raise HTTPException(403, "Управление интеграциями доступно владельцу или администратору организации")
    # Keep history, billing and revocation available when a delivery option is disabled.
    if request.url.path.startswith("/api/integrations/") and request.method not in {"GET", "DELETE"}:
        ensure_integrations_enabled(db, user.organization_id)
    return user


def billing_owner(user=Depends(get_current_user)):
    if not user.organization_id or user.organization_role != "owner":
        raise HTTPException(403, "Условия и оплата доступны владельцу организации")
    return user


@dataclass(frozen=True)
class IntegrationPrincipal:
    organization_id: int
    key_id: int
    scopes: tuple[str, ...]
    created_by: int


def require_scope(scope):
    def dependency(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)), db: Session = Depends(get_db)):
        if not credentials or not credentials.credentials.startswith("lrn_"):
            raise HTTPException(401, "Требуется интеграционный ключ", headers={"WWW-Authenticate":"Bearer"})
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        key = db.query(IntegrationKey).filter_by(key_hash=digest).first()
        if not key or key.revoked_at or key.expires_at <= datetime.utcnow():
            raise HTTPException(401, "Интеграционный ключ недействителен или истёк")
        org = db.get(Organization, key.organization_id)
        if not org or not org.is_active or scope not in key.scopes:
            raise HTTPException(403, "Ключ не разрешает это действие")
        ensure_integrations_enabled(db, org.id)
        if scope.endswith(":write"):
            from app.services.billing_service import ensure_write_access
            ensure_write_access(db, key.organization_id)
        key.last_used_at = datetime.utcnow()
        audit(db, None, "integration_key.used", "integration_key", key.id, organization_id=key.organization_id,
              method=request.method, path=request.url.path, scope=scope)
        return IntegrationPrincipal(key.organization_id, key.id, tuple(key.scopes), key.created_by)
    return dependency
