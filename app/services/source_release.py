"""Only an explicit author decision can expose original sources to learners."""
from hashlib import sha256
from urllib.parse import quote
from fastapi import HTTPException, Response
from sqlalchemy import select
from app.models.course import Course
from app.models.document import Document
from app.models.materials import PublishedSourceFile
from app.services.course_access import course_access_filter
from app.services.file_storage import get_file_storage
from app.services.image_service import course_access


def metadata(document):
    return {"document_id": document.id, "version": document.version, "content_hash": document.content_hash,
        "filename": document.original_filename, "mime_type": document.mime_type, "size_bytes": document.size_bytes}


def manifest(db, course_id):
    documents = db.query(Document).filter_by(course_id=course_id, is_deleted=False, is_current=True,
        status="indexed", learner_visible=True).order_by(Document.id).all()
    return [metadata(item) for item in documents]


def list_sources(db, user, course_id, offset=0, limit=20):
    course = course_access(db, user, course_id, allow_reviewer=True)
    query = db.query(Document).filter_by(course_id=course_id, is_deleted=False, is_current=True)
    author = bool(db.query(Course).filter(Course.id == course_id, course_access_filter(user.id)).first())
    return {"content_revision": course.content_revision, "can_edit": author, "total": query.count(),
        "items": [metadata(item) | {"learner_visible": item.learner_visible, "status": item.status,
            "content_url": f"/api/courses/{course_id}/source-release/{item.id}/content"}
            for item in query.order_by(Document.id).offset(offset).limit(limit)]}


def set_release(db, user, course_id, document_id, payload):
    # Course lock is shared with publication and source replacement.
    course = db.execute(select(Course).where(Course.id == course_id, course_access_filter(user.id),
        Course.is_deleted.is_(False)).with_for_update()).scalar_one_or_none()
    if course is None:
        raise HTTPException(404, "Курс не найден")
    from app.services.feature_access import require_feature
    require_feature(db, course.organization_id, "authoring")
    if course.content_revision != payload.expected_revision:
        raise HTTPException(409, {"code": "revision_conflict", "message": "Курс изменился. Проверьте текущие источники и повторите действие."})
    if course.status == "generating":
        raise HTTPException(409, "Остановите генерацию перед изменением доступа к источникам")
    document = db.query(Document).filter_by(id=document_id, course_id=course_id, is_current=True, is_deleted=False).first()
    if document is None:
        raise HTTPException(404, "Источник не найден")
    if document.version != payload.document_version or document.content_hash != payload.content_hash:
        raise HTTPException(409, "Версия источника изменилась; проверьте новый файл")
    if payload.learner_visible and (not payload.confirm_original_safe or document.status != "indexed"):
        raise HTTPException(422, "Проверьте оригинал: в нём не должно быть приватных ответов и материалов преподавателя")
    if document.learner_visible != payload.learner_visible:
        document.learner_visible = payload.learner_visible
        course.content_revision += 1
    db.commit()
    return {"content_revision": course.content_revision, **metadata(document), "learner_visible": document.learner_visible}


def checked_bytes(storage_key, expected_hash, expected_size):
    try:
        content = get_file_storage().read_bytes(storage_key)
    except (OSError, ValueError):
        raise HTTPException(503, "Файл временно недоступен. Сообщите администратору; версия сохранена.") from None
    if len(content) != expected_size or sha256(content).hexdigest() != expected_hash:
        raise HTTPException(503, "Проверка целостности источника не пройдена; скачивание остановлено")
    return content


def pin_sources(db, version_id, sources):
    for source in sources:
        document = db.get(Document, source["document_id"])
        if document is None or metadata(document) != source:
            raise HTTPException(409, "Источник изменился во время публикации")
        checked_bytes(document.storage_key, source["content_hash"], source["size_bytes"])
        db.add(PublishedSourceFile(course_version_id=version_id, document_id=document.id,
            document_version=document.version, content_hash=document.content_hash, storage_key=document.storage_key,
            filename=document.original_filename, mime_type=document.mime_type, size_bytes=document.size_bytes))


def download(content, filename, mime_type):
    return Response(content, media_type=mime_type, headers={"Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe="")})


def author_download(db, user, course_id, document_id):
    course_access(db, user, course_id, allow_reviewer=True)
    document = db.query(Document).filter_by(id=document_id, course_id=course_id).first()
    if not document:
        raise HTTPException(404, "Источник не найден")
    return download(checked_bytes(document.storage_key, document.content_hash, document.size_bytes), document.original_filename, document.mime_type)


def learner_download(db, user, assignment_id, document_id, request=None):
    from app.services.learning_service import learner_assignment, snapshot_for
    assignment = learner_assignment(db, user, assignment_id)
    from app.routes.mobile_auth import require_mobile_feature
    require_mobile_feature(db, assignment.organization_id, "material_downloads")
    sources = snapshot_for(db, assignment.course_version_id).get("sources", [])
    source = next((item for item in sources if item["document_id"] == document_id), None)
    pin = db.get(PublishedSourceFile, (assignment.course_version_id, document_id))
    if not source or not pin:
        raise HTTPException(404, "Источник не опубликован для этого назначения")
    if source["version"] != pin.document_version or source["content_hash"] != pin.content_hash:
        raise HTTPException(503, "Снимок источника не прошёл проверку целостности")
    if request is not None:
        from app.services.offline_files import file_response
        return file_response(request, pin.storage_key, pin.size_bytes, pin.content_hash, pin.mime_type)
    return download(checked_bytes(pin.storage_key, pin.content_hash, pin.size_bytes), pin.filename, pin.mime_type)
