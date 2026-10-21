"""Owner-confirmed, portable exports of one immutable published course version."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import HTTPException
from sqlalchemy import select
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.document import Document
from app.models.learning import PublishedLearningContent
from app.models.materials import CourseImage, PublishedSourceFile
from app.services import learning_service as learning
from app.services.organization_access import audit, lock_org
from app.services.protected_image_refs import image_ids_in
from app.services.source_release import checked_bytes

MAX_BUNDLE_BYTES = 512 * 1024 * 1024
_IMAGE_PATH = re.compile(r'''(?<![\w/])(?:/api)?/images/(\d+)/content(?=$|[\s?#)\]>"'])''')


def owner_scope(user):
    # Platform operations and general team administration do not grant export.
    if not user.organization_id or user.organization_role != 'owner' or user.is_admin:
        raise HTTPException(403, 'Передачу опубликованного курса подтверждает владелец организации')
    return user.organization_id


def version_query(db, org_id):
    return db.query(CourseVersion, PublishedLearningContent).join(Course, Course.id == CourseVersion.course_id).join(
        PublishedLearningContent, PublishedLearningContent.course_version_id == CourseVersion.id).filter(
        Course.organization_id == org_id, PublishedLearningContent.organization_id == org_id,
        CourseVersion.publication_status == 'published')


def catalogue(db, user, offset, limit):
    query = version_query(db, owner_scope(user))
    return {'total': query.count(), 'items': [{'course_id': version.course_id, 'course_version_id': version.id,
        'title': version.name, 'revision': version.revision, 'published_at': version.published_at,
        'snapshot_sha256': learning.content_digest(content.snapshot), 'source_count': len(content.snapshot.get('sources', []))}
        for version, content in query.order_by(CourseVersion.id.desc()).offset(offset).limit(limit)]}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def rewrite_images(value, paths):
    if isinstance(value, str):
        return _IMAGE_PATH.sub(lambda match: paths[int(match[1])], value)
    if isinstance(value, list):
        return [rewrite_images(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_images(item, paths) for key, item in value.items()}
    return value


def course_markdown(content, private):
    lines = ['# ' + content['course']['title'], '', content['course'].get('description', '')]
    def assessment(item):
        lines.extend(['', '### ' + item['question'], '', item.get('description', ''), '',
            f"Максимум баллов / Maximum points: {item['max_points']}"])
        lines.extend('- ' + answer for answer in item.get('answers', []))
        for criterion in item.get('criteria', []):
            lines.extend(['', f"- {criterion['title']} ({criterion['max_points']}): {criterion.get('description', '')}"])
            lines.extend(f"  - {level['title']} ({level['points']}): {level.get('description', '')}" for level in criterion.get('levels', []))
        if private and item.get('correct_answers'):
            lines.extend(['', '**Ключ ответа / Answer key — PRIVATE**', *['- ' + answer for answer in item['correct_answers']]])
    for module in content.get('modules', []):
        lines.extend(['', '## ' + module['title'], '', module.get('description', '')])
        for lesson in module.get('lessons', []):
            lines.extend(['', '### ' + lesson['title'], '', lesson.get('description', ''), '', lesson.get('content', '')])
        for item in module.get('assessments', []):
            assessment(item)
    for item in content.get('assessments', []):
        assessment(item)
    if content.get('sources'):
        lines.extend(['', '## Материалы / Materials'])
        lines.extend(f"- [{item['filename']}]({item['package_path']})" for item in content['sources'])
    return ('\n'.join(lines) + '\n').encode('utf-8')


def prepare_bundle(db, user, payload):
    org_id = owner_scope(user)
    if not payload.confirm_transfer or (payload.include_answer_keys and not payload.confirm_private_materials):
        raise HTTPException(422, 'Подтвердите состав передачи; ключи ответов требуют отдельного подтверждения')
    org = lock_org(db, org_id)
    db.refresh(user)
    if owner_scope(user) != org_id:
        raise HTTPException(403, 'Права владельца изменились. Передача остановлена.')
    if not org.is_active:
        raise HTTPException(403, 'Организация приостановлена')
    row = version_query(db, org_id).filter(CourseVersion.id == payload.course_version_id).first()
    if not row:
        raise HTTPException(404, 'Опубликованная версия не найдена')
    version, published = row
    source_digest = learning.content_digest(published.snapshot)
    if source_digest != payload.snapshot_sha256:
        raise HTTPException(409, 'Состав версии изменился. Проверьте список перед передачей.')
    # Lock course ownership while preparing/auditing; assignments and learners are
    # not queried or exported. Old versions remain independent of today's draft.
    course = db.execute(select(Course).where(Course.id == version.course_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    if course.organization_id != org_id:
        raise HTTPException(404, 'Курс недоступен в этой организации')
    content = deepcopy(published.snapshot) if payload.include_answer_keys else learning.public_snapshot(db, SimpleNamespace(course_version_id=version.id))
    export_id = str(uuid4())
    manifest = {'format': 'lernium-course-transfer', 'format_version': 1, 'export_id': export_id,
        'created_at': datetime.now(timezone.utc).isoformat(), 'course_id': course.id, 'course_version_id': version.id,
        'revision': version.revision, 'published_at': version.published_at.isoformat() if version.published_at else None,
        'snapshot_sha256': source_digest, 'includes_answer_keys': payload.include_answer_keys,
        'includes_sources': payload.include_sources, 'files': []}
    archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    total_bytes = 0
    try:
        with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as bundle:
            def put(path, data, mime, **metadata):
                nonlocal total_bytes
                total_bytes += len(data)
                if total_bytes > MAX_BUNDLE_BYTES:
                    raise HTTPException(413, 'Пакет превышает 512 MiB. Выгрузите курс без оригиналов, затем скачайте материалы отдельно.')
                bundle.writestr(path, data)
                manifest['files'].append({'path': path, 'size_bytes': len(data), 'sha256': sha256(data).hexdigest(), 'media_type': mime, **metadata})
            image_paths = {}
            from app.services.image_service import storage
            for image_id in sorted(image_ids_in(content)):
                image = db.get(CourseImage, image_id)
                if not image or image.course_id != course.id or image.status != 'approved' or not image.storage_key:
                    raise HTTPException(409, 'Версия содержит недоступную иллюстрацию. Передача остановлена.')
                try:
                    data = storage().read_bytes(image.storage_key)
                except (OSError, ValueError):
                    raise HTTPException(503, 'Файл иллюстрации временно недоступен; неполный пакет не выдан') from None
                extension = '.png' if image.mime_type == 'image/png' else '.jpg'
                path = f'images/image-{image.id}{extension}'
                put(path, data, image.mime_type, image_id=image.id)
                image_paths[image.id] = path
            content = rewrite_images(content, image_paths)
            exported_sources = []
            if payload.include_sources:
                for source in content.get('sources', []):
                    pin = db.get(PublishedSourceFile, (version.id, source['document_id']))
                    document = db.get(Document, source['document_id'])
                    if not pin or not document or document.course_id != course.id or pin.document_version != source['version'] or pin.content_hash != source['content_hash']:
                        raise HTTPException(409, 'Закреплённый источник не соответствует этой версии; передача остановлена')
                    data = checked_bytes(pin.storage_key, pin.content_hash, pin.size_bytes)
                    extension = {'application/pdf': '.pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                        'text/plain': '.txt', 'text/markdown': '.md'}.get(pin.mime_type, '.bin')
                    path = f'sources/document-{pin.document_id}-v{pin.document_version}{extension}'
                    put(path, data, pin.mime_type, document_id=pin.document_id, document_version=pin.document_version, original_filename=pin.filename)
                    exported_sources.append({**source, 'package_path': path})
            content['sources'] = exported_sources
            put('course.json', encoded(content), 'application/json')
            put('course.md', course_markdown(content, payload.include_answer_keys), 'text/markdown')
            readme = ('Lernium — передача опубликованного курса / published course transfer\n\n'
                'course.md: читаемый курс с относительными ссылками / readable content with relative asset links.\n'
                'course.json: структурированное содержание / structured content.\n'
                'manifest.json: версия формата, состав и SHA-256 файлов / format version, inventory and file checksums.\n'
                'SHA256SUMS: проверка целостности / integrity check (sha256sum -c SHA256SUMS).\n'
                'Участники, ответы и результаты отсутствуют / No participants, submissions or results included.\n'
                'Оригиналы включены только из разрешённого опубликованного снимка / Only explicitly released originals are included.\n'
                'Внешние ссылки в авторском тексте сохраняются и могут требовать сети / External author links are preserved and may require a network connection.\n'
                + ('PRIVATE: содержит ключи ответов; передавайте только уполномоченным получателям.\n' if payload.include_answer_keys else 'Пакет не содержит закрытые ключи ответов / Private answer keys excluded.\n'))
            put('README.txt', readme.encode('utf-8'), 'text/plain')
            manifest_bytes = encoded(manifest)
            bundle.writestr('manifest.json', manifest_bytes)
            sums = ''.join(f"{entry['sha256']}  {entry['path']}\n" for entry in manifest['files'])
            sums += f"{sha256(manifest_bytes).hexdigest()}  manifest.json\n"
            bundle.writestr('SHA256SUMS', sums.encode('ascii'))
        audit(db, user, 'learning.course_export_prepared', 'course_version', version.id, organization_id=org_id,
            course_id=course.id, export_id=export_id, snapshot_sha256=source_digest, manifest_sha256=sha256(manifest_bytes).hexdigest(),
            include_sources=payload.include_sources, include_answer_keys=payload.include_answer_keys, files=len(manifest['files']), bytes=total_bytes)
        db.commit()
        archive.seek(0)
        return archive, f'lernium-course-{course.id}-v{version.revision}.zip'
    except BaseException:
        archive.close()
        raise
