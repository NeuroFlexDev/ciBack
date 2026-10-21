"""Authenticated immutable image assets; generation is idempotent and reviewable."""
from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO
import re
from uuid import uuid4
import warnings

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.ai import providers
from app.core.config import settings
from app.models.materials import CourseImage
from app.models.course import Course
from app.models.user import User
from app.services.course_access import course_access_filter
from app.services.file_storage import LocalFileStorage


def course_access(db, user, course_id, *, allow_reviewer=False):
    course = db.query(Course).filter(Course.id == course_id, course_access_filter(user.id),
                                     Course.is_deleted.is_(False)).first()
    if not course and allow_reviewer:
        from app.services.learning_service import can_scope
        candidate = db.get(Course, course_id)
        if candidate and not candidate.is_deleted and candidate.organization_id is not None and candidate.organization_id == user.organization_id and can_scope(db, user, "reviewer", course_id=course_id):
            course = candidate
    if not course:
        raise HTTPException(404, "Курс не найден")
    return course


def image_out(item):
    return {key: getattr(item, key) for key in ("id", "course_id", "status", "mime_type", "size_bytes",
            "width", "height", "prompt", "error_code", "review_comment", "created_at", "updated_at")} | {
        "content_url": f"/api/images/{item.id}/content" if item.storage_key else None}


def storage():
    return LocalFileStorage(settings.UPLOAD_DIR, settings.IMAGE_MAX_BYTES)


def normalize_image(content):
    if not content or len(content) > settings.IMAGE_MAX_BYTES:
        raise HTTPException(413, "Иллюстрация превышает допустимый размер")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as picture:
                if picture.format not in {"PNG", "JPEG"}:
                    raise HTTPException(415, "Поддерживаются только PNG и JPEG")
                width, height = picture.size
                if width * height > settings.IMAGE_MAX_PIXELS or width < 1 or height < 1:
                    raise HTTPException(413, "Иллюстрация превышает лимит пикселей")
                picture.verify()
            with Image.open(BytesIO(content)) as picture:
                fmt = "PNG" if picture.format == "PNG" else "JPEG"
                converted = picture.convert("RGBA" if fmt == "PNG" else "RGB")
                output = BytesIO()
                converted.save(output, format=fmt)
                result = output.getvalue()
                if len(result) > settings.IMAGE_MAX_BYTES:
                    raise HTTPException(413, "Декодированная иллюстрация превышает лимит")
                return result, "image/png" if fmt == "PNG" else "image/jpeg", width, height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "Повреждённое или недопустимое изображение") from exc


def save_content(item, content):
    content, mime, width, height = normalize_image(content)
    key = f"course-images/{item.owner_id}/{uuid4().hex}"
    stored = storage().save(BytesIO(content), key)
    item.storage_key, item.mime_type = key, mime
    item.width, item.height, item.size_bytes = width, height, stored.size_bytes
    item.status, item.error_code = "pending_review", None


def image_access(db, user, image_id, *, author_only=False):
    query = db.query(CourseImage).filter(CourseImage.id == image_id)
    item = (query.with_for_update() if author_only else query).first()
    if not item:
        raise HTTPException(404, "Иллюстрация не найдена")
    if db.query(Course).filter(Course.id == item.course_id, course_access_filter(user.id), Course.is_deleted.is_(False)).first():
        return item
    from app.services.learning_service import can_scope
    course = db.get(Course, item.course_id)
    if course and not course.is_deleted and course.organization_id is not None and course.organization_id == user.organization_id and can_scope(db, user, "reviewer", course_id=course.id):
        return item
    if not author_only and item.status == "approved":
        from app.services.learning_service import authorized_learning_image
        if authorized_learning_image(db, user, image_id):
            return item
    raise HTTPException(404, "Иллюстрация не найдена")


def prepare_generation(db, user, course_id, prompt, request_id):
    from app.services.feature_access import require_feature
    from app.services.organization_access import lock_member
    user = lock_member(db, user.id, expected_auth_version=user.auth_version)
    require_feature(db, user.organization_id, "images")
    course_access(db, user, course_id)
    # A user row serializes quotas even for personal accounts; organization row
    # serializes across its members. Failed/uncertain provider calls consume quota.
    owner_ids = [user.id]
    if user.organization_id:
        from app.services.organization_access import lock_org, member_ids
        org = lock_org(db, user.organization_id)
        if not org.is_active:
            raise HTTPException(403, "Организация приостановлена")
        owner_ids = member_ids(db, org.id)
    existing = db.query(CourseImage).filter_by(owner_id=user.id, request_id=request_id).first()
    if existing:
        if existing.course_id != course_id or existing.prompt != prompt:
            raise HTTPException(409, "Этот ключ запроса уже использован для другой иллюстрации")
        return existing, False
    provider = providers.registry().get(settings.IMAGE_PROVIDER)
    if not provider or not providers.available(provider) or not settings.IMAGE_MODEL:
        raise HTTPException(503, {"code": "image_provider_unavailable", "message": "Генератор изображений недоступен. Можно загрузить PNG или JPEG."})
    since = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    scope = Course.organization_id == user.organization_id
    if user.organization_id is None:
        scope = scope & (Course.owner_id == user.id)
    used = db.query(CourseImage).join(Course).filter(scope, CourseImage.prompt.is_not(None),
                                                   CourseImage.created_at >= since).count()
    from app.services.material_quota import material_policy, check_storage
    if used >= material_policy(db, user)["image_monthly_limit"]:
        raise HTTPException(429, "Исчерпан месячный лимит генерации иллюстраций")
    check_storage(db, user, settings.IMAGE_MAX_BYTES)
    item = CourseImage(owner_id=user.id, course_id=course_id, prompt=prompt, request_id=request_id,
                       status="queued", provider=provider.id, model=settings.IMAGE_MODEL, size_bytes=settings.IMAGE_MAX_BYTES)
    from app.services.ai_resources import reserve
    if not provider.local and settings.IMAGE_PRICE_RUB is None:
        raise HTTPException(503, {"code": "image_price_unconfigured", "message": "Не настроен денежный лимит одного изображения"})
    db.add(item); db.flush()
    reserve(db, owner_id=user.id, course_id=course_id, operation="image", lane="background",
        key=f"image:{item.id}", queued=True, cost=0 if provider.local else settings.IMAGE_PRICE_RUB)
    db.commit(); db.refresh(item)
    return item, True


def execute_image_generation(image_id):
    from app.database.db import SessionLocal
    from openai import OpenAI
    db = SessionLocal()
    item = None
    claimed = False
    request_started = False
    try:
        item = db.query(CourseImage).filter_by(id=image_id).with_for_update().first()
        if item is None or item.status != "queued":
            return
        from app.services.ai_resources import claim
        claimed = claim(db, f"image:{image_id}")
        if not claimed:
            return
        from app.services.ai_access_boundary import principal_stamp, author_result_access
        owner = db.get(User, item.owner_id)
        if owner is None:
            raise RuntimeError("image_owner_unavailable")
        owner_id, course_id, stamp = owner.id, item.course_id, principal_stamp(owner)
        author_result_access(db, owner_id, stamp, course_id, "images")
        provider = providers.registry().get(item.provider)
        if not provider or not providers.available(provider):
            raise RuntimeError("provider_unavailable")
        item.status = "generating"
        db.commit()  # At-most-once submission: a killed worker never blindly bills again.
        client = OpenAI(api_key=providers.credential(provider), base_url=str(provider.base_url),
                        timeout=provider.timeout_seconds, max_retries=0)
        request_started = True
        result = client.images.generate(model=item.model, prompt=item.prompt, n=1, size="1024x1024",
                                        response_format="b64_json")
        author_result_access(db, owner_id, stamp, course_id, "images")
        encoded = result.data[0].b64_json if result.data else None
        if not encoded or len(encoded) > settings.IMAGE_MAX_BYTES * 4 // 3 + 16:
            raise RuntimeError("invalid_image_response")
        save_content(item, base64.b64decode(encoded, validate=True))
        db.commit()
    except Exception as exc:
        db.rollback()
        from app.services.ai_resources import CapacityBusy
        if isinstance(exc, CapacityBusy) and not claimed:
            from rq import get_current_job
            job = get_current_job()
            if job is not None and not job.retries_left:
                item = db.get(CourseImage, image_id)
                item.status, item.error_code, item.size_bytes = "failed", "image_capacity_unavailable", 0
                from app.services.ai_resources import finish
                finish(db, f"image:{image_id}", unsent=True)
                return
            raise
        if item is not None:
            item = db.get(CourseImage, image_id)
            item.status = "failed"
            item.size_bytes = 0
            item.error_code = "image_access_revoked" if isinstance(exc, HTTPException) and exc.status_code in {403, 404} else "image_generation_failed"
            db.commit()
    finally:
        if claimed:
            from app.services.ai_resources import finish
            finish(db, f"image:{image_id}", unsent=not request_started, failed=db.get(CourseImage, image_id).status == "failed")
        db.close()


def enqueue_image(image_id):
    if settings.JOB_EAGER:
        execute_image_generation(image_id)
        return
    from redis import Redis
    from rq import Queue, Retry
    queue = Queue(settings.GENERATION_QUEUE_NAME, connection=Redis.from_url(settings.REDIS_URL))
    queue.enqueue("app.services.image_service.execute_image_generation", image_id,
                  job_id=f"course-image-{image_id}", job_timeout=360, result_ttl=86400, failure_ttl=604800,
                  retry=Retry(max=20, interval=15))


def validate_course_images(db, course_id):
    course_id = getattr(course_id, "id", course_id)
    from app.services.learning_service import build_publication_snapshot
    from app.services.protected_image_refs import image_ids_in
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Курс не найден")
    # The same snapshot is rendered in author review, learner and support views.
    # Theory-only validation misses references in task descriptions and rubrics.
    modules = sorted((module for module in course.modules if not module.is_deleted), key=lambda module: (module.position, module.id))
    ids = image_ids_in(build_publication_snapshot(db, course, modules))
    if not ids:
        return
    approved = {item.id for item in db.query(CourseImage).filter(CourseImage.id.in_(ids),
                 CourseImage.course_id == course_id, CourseImage.status == "approved").all()}
    if approved != ids:
        raise HTTPException(409, {"code": "images_need_review", "message": "Проверьте и утвердите все иллюстрации перед публикацией", "image_ids": sorted(ids - approved)})
