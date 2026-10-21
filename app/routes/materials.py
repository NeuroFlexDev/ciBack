from typing import Literal
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.core.config import settings
from app.models.materials import CourseImage
from app.services.auth_service import get_current_user, require_admin
from app.services import image_service as images
from app.services.feature_access import require_feature, feature_enabled

router = APIRouter()


@router.get("/materials/capabilities")
def material_capabilities(db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.ai import providers
    provider = providers.registry().get(settings.IMAGE_PROVIDER)
    return {"document_formats": ["pdf", "docx", "txt", "md"], "document_max_bytes": settings.max_document_bytes,
            "source_characters_limit": settings.AI_MAX_SOURCE_CHARS,
            "image_formats": ["png", "jpg", "jpeg"], "image_max_bytes": settings.IMAGE_MAX_BYTES,
            "image_max_pixels": settings.IMAGE_MAX_PIXELS,
            "image_library_available": feature_enabled(db, user.organization_id, "images"),
            "image_generation_available": bool(feature_enabled(db, user.organization_id, "images") and provider and providers.available(provider) and settings.IMAGE_MODEL),
            "image_monthly_limit": settings.IMAGE_MONTHLY_LIMIT,
            "course_modules_limit": settings.MAX_COURSE_MODULES, "course_lessons_limit": settings.MAX_COURSE_LESSONS,
            "lesson_content_characters_limit": settings.MAX_LESSON_CONTENT_CHARS,
            "canvas_semantics": "Canvas stores layout and connections; course content is edited through module and lesson APIs."}


class ImageGenerate(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ImageReview(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=2000)


@router.get("/courses/{course_id}/images")
def list_images(course_id: int, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), user=Depends(get_current_user)):
    images.course_access(db, user, course_id)
    query = db.query(CourseImage).filter_by(course_id=course_id)
    return {"items": [images.image_out(item) for item in query.order_by(CourseImage.id.desc()).offset(offset).limit(limit)],
            "total": query.count(), "limit": limit, "offset": offset}


@router.post("/courses/{course_id}/images", status_code=201)
def upload_image(course_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_feature(db, user.organization_id, "images")
    images.course_access(db, user, course_id)
    content = file.file.read(settings.IMAGE_MAX_BYTES + 1)
    content, _, _, _ = images.normalize_image(content)
    from app.services.material_quota import check_storage
    check_storage(db, user, len(content))
    item = CourseImage(course_id=course_id, owner_id=user.id, status="pending_review")
    images.save_content(item, content)
    try:
        db.add(item); db.commit(); db.refresh(item)
    except Exception:
        images.storage().delete(item.storage_key)
        raise
    return images.image_out(item)


@router.post("/courses/{course_id}/images/generate", status_code=202)
def generate_image(course_id: int, payload: ImageGenerate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item, created = images.prepare_generation(db, user, course_id, payload.prompt.strip(), payload.request_id)
    if created:
        try:
            images.enqueue_image(item.id)
        except Exception:
            item.status, item.error_code = "failed", "image_queue_unavailable"
            item.size_bytes = 0
            db.commit()
    return images.image_out(item)


@router.get("/images/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = images.image_access(db, user, image_id)
    # A worker killed after a paid request must not leave the UI spinning forever.
    from datetime import datetime, timedelta
    if item.status == "generating" and item.updated_at < datetime.utcnow() - timedelta(minutes=10):
        item.status, item.error_code = "failed", "image_result_unknown"
        item.size_bytes = 0
        db.commit()
    return images.image_out(item)


@router.get("/images/{image_id}/content")
def image_content(image_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = images.image_access(db, user, image_id)
    if not item.storage_key:
        raise HTTPException(409, "Иллюстрация ещё не готова")
    try:
        content = images.storage().read_bytes(item.storage_key)
    except FileNotFoundError:
        raise HTTPException(503, "Файл временно недоступен") from None
    return Response(content, media_type=item.mime_type,
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/images/{image_id}/review")
def review_image(image_id: int, payload: ImageReview, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_feature(db, user.organization_id, "images")
    item = images.image_access(db, user, image_id, author_only=True)
    if item.status != "pending_review":
        raise HTTPException(409, "Результат уже проверен или ещё не готов. Для новой версии загрузите новую иллюстрацию.")
    item.status, item.review_comment, item.reviewed_by = payload.decision, payload.comment, user.id
    from app.services.organization_access import audit
    audit(db, user, "image.review", "course_image", item.id, decision=payload.decision)
    db.commit()
    return images.image_out(item)


class MaterialPolicyInput(BaseModel):
    disk_bytes_limit: int = Field(ge=0, le=1024**5)
    image_monthly_limit: int = Field(ge=0, le=1000000)
    chat_monthly_limit: int = Field(ge=0, le=10000000)


@router.get("/admin/organizations/{organization_id}/material-policy")
def get_policy(organization_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    from app.models.materials import OrganizationMaterialPolicy
    from app.services.organization_access import lock_org, member_ids
    from app.services.material_quota import used_disk_bytes
    lock_org(db, organization_id)
    row = db.get(OrganizationMaterialPolicy, organization_id)
    return {"disk_bytes_limit": row.disk_bytes_limit if row else 5 * 1024**3,
            "image_monthly_limit": row.image_monthly_limit if row else settings.IMAGE_MONTHLY_LIMIT,
            "chat_monthly_limit": row.chat_monthly_limit if row else settings.AI_CHAT_MONTHLY_LIMIT,
            "used_disk_bytes": used_disk_bytes(db, [], organization_id=organization_id)}


@router.put("/admin/organizations/{organization_id}/material-policy")
def put_policy(organization_id: int, payload: MaterialPolicyInput, db: Session = Depends(get_db), user=Depends(require_admin)):
    from app.models.materials import OrganizationMaterialPolicy
    from app.services.organization_access import lock_org, audit
    lock_org(db, organization_id)
    row = db.get(OrganizationMaterialPolicy, organization_id) or OrganizationMaterialPolicy(organization_id=organization_id)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.add(row)
    audit(db, user, "organization.material_policy", "organization", organization_id, **payload.model_dump())
    db.commit()
    return get_policy(organization_id, db, user)
