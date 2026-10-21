"""Re-authorize after a slow external request, before exposing its result."""
from fastapi import HTTPException
from app.models.user import User
from app.models.course import Course
from app.models.administration import Organization
from app.services.course_access import course_access_filter
from app.services.feature_access import require_feature


def principal_stamp(user):
    return user.organization_id, user.auth_version


def refresh_principal(db, user_id, stamp):
    # Call only after usage/receipts have been committed. The provider wait may
    # span a membership, account, rule or token revocation in another process.
    db.expire_all()
    user = db.get(User, user_id)
    if not user or not user.is_active or principal_stamp(user) != stamp:
        raise HTTPException(403, {"code": "ai_access_revoked", "message": "Доступ изменился во время запроса. Результат не выдан; повторная оплата не выполнялась."})
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    if user.organization_id and (not org or not org.is_active):
        raise HTTPException(403, {"code": "ai_access_revoked", "message": "Доступ к организации приостановлен"})
    return user


def author_result_access(db, user_id, stamp, course_id, role):
    user = refresh_principal(db, user_id, stamp)
    if role in {"chat", "canvas", "memory"}:
        require_feature(db, user.organization_id, "assistant")
    if role == "images":
        require_feature(db, user.organization_id, "images")
        require_feature(db, user.organization_id, "authoring")
    if course_id is not None and not db.query(Course.id).filter(Course.id == course_id,
            Course.is_deleted.is_(False), course_access_filter(user_id)).first():
        raise HTTPException(403, {"code": "ai_access_revoked", "message": "Доступ к курсу отозван во время запроса"})
    return user
