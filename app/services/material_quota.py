from fastapi import HTTPException
from sqlalchemy import func
from app.core.config import settings
from app.models.materials import CourseImage, OrganizationMaterialPolicy
from app.models.document import Document
from app.models.course import Course
from app.services.organization_access import lock_org, lock_member, member_ids


def material_policy(db, user):
    if user.organization_id:
        row = db.get(OrganizationMaterialPolicy, user.organization_id)
        if row:
            return {name: getattr(row, name) for name in ("disk_bytes_limit", "image_monthly_limit", "chat_monthly_limit")}
    return {"disk_bytes_limit": 5 * 1024**3, "image_monthly_limit": settings.IMAGE_MONTHLY_LIMIT,
            "chat_monthly_limit": settings.AI_CHAT_MONTHLY_LIMIT}


def used_disk_bytes(db, owner_ids, *, organization_id=None):
    # Archived versions remain on disk and count until retention safely deletes
    # them. Soft-delete is not a way to bypass a real storage quota.
    courses = db.query(Course.id).filter(Course.organization_id == organization_id)
    if organization_id is None:
        courses = courses.filter(Course.owner_id.in_(owner_ids))
    return (db.query(func.coalesce(func.sum(Document.size_bytes), 0)).filter(Document.course_id.in_(courses)).scalar()
            + db.query(func.coalesce(func.sum(CourseImage.size_bytes), 0)).filter(CourseImage.course_id.in_(courses)).scalar())


def check_storage(db, user, additional_bytes):
    user = lock_member(db, user.id, expected_auth_version=user.auth_version)
    owner_ids = [user.id]
    if user.organization_id:
        org = lock_org(db, user.organization_id)
        if not org.is_active:
            raise HTTPException(403, "Организация приостановлена")
        owner_ids = member_ids(db, org.id)
    if used_disk_bytes(db, owner_ids, organization_id=user.organization_id) + additional_bytes > material_policy(db, user)["disk_bytes_limit"]:
        raise HTTPException(413, {"code": "storage_quota_exceeded", "message": "Недостаточно места в хранилище организации. Обратитесь к администратору."})
