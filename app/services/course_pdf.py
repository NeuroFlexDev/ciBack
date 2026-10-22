"""PDF export shares ZIP ownership, snapshot, consent and immutable-asset checks."""
from hashlib import sha256
import json
from zipfile import ZipFile
from threading import BoundedSemaphore
from fastapi import HTTPException
from app.services import course_transfer as transfer
from app.services.pdf_assets import PDFRenderError, render_inventory
from app.services.pdf_document import build_pdf
from app.services.organization_access import audit

# CPU/memory bounded per API process; reject overload before locking tenant data.
PDF_SLOTS = BoundedSemaphore(2)


def bundle_content(db, user, payload, claims):
    # PDF contains course content, not attachments. Original source exports use ZIP.
    selection = payload.model_copy(update={'include_sources': False})
    archive, _ = transfer.prepare_bundle(db, user, selection, claims=claims, record_export=False)
    try:
        with ZipFile(archive) as bundle:
            if bundle.getinfo('course.json').file_size > 8 * 1024 * 1024:
                raise PDFRenderError('Содержание курса превышает лимит PDF.')
            content = json.loads(bundle.read('course.json'))
            metadata = json.loads(bundle.read('manifest.json'))
            images = {}
            total = 0
            for item in metadata['files']:
                if item['path'].startswith('images/'):
                    total += item['size_bytes']
                    if total > 64 * 1024 * 1024:
                        raise PDFRenderError('Иллюстрации PDF превышают 64 MiB. Оптимизируйте изображения.')
                    images[item['path']] = bundle.read(item['path'])
            return content, metadata, images
    finally:
        archive.close()


def export_pdf(db, user, payload, claims, *, plan=False):
    org_id = transfer.owner_scope(user)
    if not PDF_SLOTS.acquire(blocking=False):
        raise HTTPException(503, 'Сервис PDF занят. Повторите скачивание через несколько секунд.', headers={'Retry-After': '10'})
    try:
        content, metadata, images = bundle_content(db, user, payload, claims)
        diagrams, _ = render_inventory(content)
        if plan:
            result = {'snapshot_sha256': metadata['snapshot_sha256'], 'diagrams': diagrams,
                      'course_title': content['course']['title'], 'course_version_id': metadata['course_version_id']}
        else:
            supplied = {item.id: item.svg for item in payload.diagrams}
            if len(supplied) != len(payload.diagrams):
                raise PDFRenderError('Схема указана повторно.')
            result = build_pdf(content, images, metadata, supplied)
        user = transfer.check_owner_session(db, user.id, org_id, claims)
        audit(db, user, 'learning.course_pdf_plan_prepared' if plan else 'learning.course_pdf_exported',
              'course_version', metadata['course_version_id'], organization_id=org_id,
              course_id=metadata['course_id'], snapshot_sha256=metadata['snapshot_sha256'],
              include_answer_keys=metadata['includes_answer_keys'], diagram_count=len(diagrams),
              **({} if plan else {'pdf_sha256': sha256(result).hexdigest(), 'bytes': len(result)}))
        db.commit()
        return result if plan else (result, f"lernium-course-{metadata['course_id']}-v{metadata['revision']}.pdf")
    except PDFRenderError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    finally:
        PDF_SLOTS.release()
