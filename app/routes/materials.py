from typing import Literal
from decimal import Decimal
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Response, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.core.config import settings
from app.models.materials import CourseImage
from app.services.auth_service import get_current_user, require_admin
from app.services import image_service as images
from app.services.feature_access import require_feature, feature_enabled

router = APIRouter()


class SourceRelease(BaseModel):
    learner_visible: bool
    confirm_original_safe: bool = False
    expected_revision: int = Field(ge=0)
    document_version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=128)


@router.get("/courses/{course_id}/source-release")
def source_release_list(course_id: int, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.source_release import list_sources
    return list_sources(db, user, course_id, offset, limit)


@router.put("/courses/{course_id}/source-release/{document_id}")
def source_release_update(course_id: int, document_id: int, payload: SourceRelease,
        db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.source_release import set_release
    return set_release(db, user, course_id, document_id, payload)


@router.get("/courses/{course_id}/source-release/{document_id}/content")
def source_release_content(course_id: int, document_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.source_release import author_download
    return author_download(db, user, course_id, document_id)


@router.get("/learning/assignments/{assignment_id}/sources/{document_id}/content")
def learner_source_content(assignment_id: int, document_id: int, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.source_release import learner_download
    return learner_download(db, user, assignment_id, document_id, request)


@router.get("/learning/assignments/{assignment_id}/media/{media_id}/content")
def learner_media_content(assignment_id: int, media_id: int, request: Request,
        db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.learning_service import learner_assignment, snapshot_for
    from app.models.materials import CourseImage
    from app.models.course_version import CourseVersion
    assignment = learner_assignment(db, user, assignment_id)
    from app.routes.mobile_auth import require_mobile_feature
    require_mobile_feature(db, assignment.organization_id, "material_downloads")
    require_mobile_feature(db, assignment.organization_id, "offline_access")
    pinned = next((item for item in snapshot_for(db, assignment.course_version_id).get("media", [])
        if item.get("id") == media_id), None)
    image = db.get(CourseImage, media_id)
    version = db.get(CourseVersion, assignment.course_version_id)
    if not pinned or not image or not version or image.course_id != version.course_id:
        raise HTTPException(404, "Published media not found for this assignment")
    from app.services.offline_files import file_response
    return file_response(request, pinned['storage_key'], pinned['size_bytes'], pinned['sha256'], pinned['mime_type'])


@router.get("/materials/resource-usage")
def resource_usage(db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.ai_resources import usage_summary
    return usage_summary(db, user.id)


@router.get("/materials/capabilities")
def material_capabilities(db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.ai import providers
    provider = providers.registry().get(settings.IMAGE_PROVIDER)
    from app.services.material_quota import material_policy
    price_ready = bool(provider and (provider.local or settings.IMAGE_PRICE_RUB is not None))
    configured = bool(provider and providers.available(provider) and settings.IMAGE_MODEL)
    return {"document_formats": ["pdf", "docx", "txt", "md"], "document_max_bytes": settings.max_document_bytes,
            "web_search_supported": True,
            "source_characters_limit": settings.AI_MAX_SOURCE_CHARS,
            "image_formats": ["png", "jpg", "jpeg", "webp"], "image_max_bytes": settings.IMAGE_MAX_BYTES,
            "image_max_pixels": settings.IMAGE_MAX_PIXELS,
            "image_library_available": feature_enabled(db, user.organization_id, "images"),
            "image_generation_available": bool(feature_enabled(db, user.organization_id, "images") and configured and price_ready),
            "image_generation_error": None if configured and price_ready else "image_price_unconfigured" if configured else "image_provider_unavailable",
            "image_price_rub_upper_bound": str(settings.IMAGE_PRICE_RUB) if configured and price_ready and not provider.local else "0" if configured and provider.local else None,
            "image_monthly_limit": material_policy(db, user)["image_monthly_limit"],
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
    course = images.course_access(db, user, course_id, allow_reviewer=True)
    from app.models.course import Course
    from app.services.course_access import course_access_filter
    from app.services.learning_service import settings_for, can_scope
    author = bool(db.query(Course).filter(Course.id == course_id, course_access_filter(user.id)).first())
    enabled = feature_enabled(db, user.organization_id, "images")
    independent = bool(course.organization_id and settings_for(db, course.organization_id).rules.get("require_independent_approval"))
    reviewer = bool(course.organization_id and can_scope(db, user, "reviewer", course_id=course.id))
    query = db.query(CourseImage).filter_by(course_id=course_id)
    return {"items": [images.image_out(item) | {
                "can_approve": enabled and (not independent or reviewer and user.id not in {item.owner_id, course.owner_id}),
                "can_reject": enabled,
            } for item in query.order_by(CourseImage.id.desc()).offset(offset).limit(limit)],
            "total": query.count(), "limit": limit, "offset": offset,
            "can_upload": author and enabled and feature_enabled(db, user.organization_id, "authoring"),
            "can_generate": author and enabled and feature_enabled(db, user.organization_id, "authoring"),
            "independent_review_required": independent}


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
            db.rollback()
            item = images.reconcile_enqueue_failure(db, item.id)
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
        from app.services.ai_resources import finish
        finish(db, f"image:{item.id}", failed=True)
    result = images.image_out(item)
    try:
        images.course_access(db, user, item.course_id, allow_reviewer=True)
    except HTTPException:
        # Assignment access permits viewing the approved illustration, never
        # the teacher's generation prompt or private review discussion.
        result = {key: value for key, value in result.items() if key in {
            "id", "status", "mime_type", "size_bytes", "width", "height", "content_url"}}
    return result


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
    if user.organization_id and payload.decision == "approved":
        from app.services.learning_service import settings_for, can_scope
        from app.models.course import Course
        course = db.get(Course, item.course_id)
        if settings_for(db, user.organization_id).rules.get("require_independent_approval") and (
            user.id in {item.owner_id, course.owner_id} or not can_scope(db, user, "reviewer", course_id=course.id)):
            raise HTTPException(403, "Иллюстрацию должен утвердить независимый проверяющий курса")
    if item.status != "pending_review":
        raise HTTPException(409, "Результат уже проверен или ещё не готов. Для новой версии загрузите новую иллюстрацию.")
    item.status, item.review_comment, item.reviewed_by = payload.decision, payload.comment, user.id
    from app.services.organization_access import audit
    audit(db, user, "image.review", "course_image", item.id, decision=payload.decision)
    db.commit()
    return images.image_out(item)


class ResourceLimitsInput(BaseModel):
    queued_limit: int = Field(ge=0, le=100000)
    background_limit: int = Field(ge=0, le=1000)
    interactive_limit: int = Field(ge=0, le=1000)
    monthly_tokens: int = Field(ge=0, le=10**12)
    monthly_ocr_pages: int = Field(ge=0, le=10**8)
    monthly_rub: Decimal = Field(ge=0, le=10**10, allow_inf_nan=False, decimal_places=6)


class MaterialPolicyInput(BaseModel):
    disk_bytes_limit: int = Field(ge=0, le=1024**5)
    image_monthly_limit: int = Field(ge=0, le=1000000)
    chat_monthly_limit: int = Field(ge=0, le=10000000)
    resource_limits: ResourceLimitsInput | None = None
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


@router.get("/admin/organizations/{organization_id}/material-policy")
def get_policy(organization_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    from app.models.materials import OrganizationMaterialPolicy
    from app.services.organization_access import lock_org, member_ids
    from app.services.material_quota import used_disk_bytes
    lock_org(db, organization_id)
    row = db.get(OrganizationMaterialPolicy, organization_id)
    from app.services.ai_resources import organization_limits
    limits = organization_limits(db, organization_id)
    result = {"disk_bytes_limit": row.disk_bytes_limit if row else 5 * 1024**3,
            "image_monthly_limit": row.image_monthly_limit if row else settings.IMAGE_MONTHLY_LIMIT,
            "chat_monthly_limit": row.chat_monthly_limit if row else settings.AI_CHAT_MONTHLY_LIMIT,
            "used_disk_bytes": used_disk_bytes(db, [], organization_id=organization_id),
            "resource_limits": {key: str(value) if isinstance(value, Decimal) else value for key, value in limits.items()}}
    import hashlib, json
    result["revision"] = hashlib.sha256(json.dumps({key: value for key, value in result.items() if key != "used_disk_bytes"}, sort_keys=True).encode()).hexdigest()
    return result


@router.put("/admin/organizations/{organization_id}/material-policy")
def put_policy(organization_id: int, payload: MaterialPolicyInput, db: Session = Depends(get_db), user=Depends(require_admin)):
    from app.models.materials import OrganizationMaterialPolicy
    from app.services.organization_access import lock_org, audit
    lock_org(db, organization_id)
    if payload.expected_revision is not None and get_policy(organization_id, db, user)["revision"] != payload.expected_revision:
        raise HTTPException(409, {"code": "resource_policy_conflict", "message": "Квоты изменены другим оператором. Ваш черновик сохранён; сравните текущие значения."})
    row = db.get(OrganizationMaterialPolicy, organization_id) or OrganizationMaterialPolicy(organization_id=organization_id)
    for key, value in payload.model_dump(mode="json", exclude_none=True, exclude={"expected_revision"}).items():
        setattr(row, key, value)
    db.add(row)
    audit(db, user, "organization.material_policy", "organization", organization_id, **payload.model_dump(mode="json", exclude_none=True))
    db.commit()
    return get_policy(organization_id, db, user)
