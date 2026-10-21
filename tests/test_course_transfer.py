from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile
import pytest
from app.core.config import settings
from app.models.administration import AdminEvent
from app.models.document import Document
from app.models.learning import PublishedLearningContent
from app.models.materials import CourseImage, PublishedSourceFile
from app.models.user import User
from app.services import image_service
from tests.test_learning_runtime import world, publish, headers
from tests.test_source_release import document

BASE = '/api/team/course-transfers'


def selection(client, world, version_id):
    response = client.get(BASE, headers=headers(world['owner']))
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()['items'] if row['course_version_id'] == version_id)
    return {'course_version_id': version_id, 'snapshot_sha256': item['snapshot_sha256'], 'confirm_transfer': True}


def test_owner_exports_pinned_portable_version_with_complete_checksums_and_audit(client, db_session, world, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, _, _, _, task, _ = publish(client, db_session, world['author'])
    source = document(db_session, course, world['author'], b'Approved original bytes')
    source.learner_visible = True
    image = CourseImage(course_id=course.id, owner_id=world['author'].id, status='approved', storage_key='image.png', mime_type='image/png')
    db_session.add(image); db_session.flush()
    task.description = f'Published exercise ![illustration](/images/{image.id}/content)\nExpected answer: PRIVATE EXEMPLAR'
    course.content_revision += 1
    db_session.commit()
    monkeypatch.setattr(image_service, 'storage', lambda: SimpleNamespace(read_bytes=lambda key: b'approved PNG bytes'))
    assert client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author'])).status_code == 200
    version_id = db_session.query(PublishedLearningContent).order_by(PublishedLearningContent.course_version_id.desc()).first().course_version_id
    private_bytes = b'PRIVATE UNPUBLISHED MATERIAL'
    (tmp_path/'private.txt').write_bytes(private_bytes)
    db_session.add(Document(owner_id=world['author'].id, course_id=course.id, storage_key='private.txt',
        content_hash=sha256(private_bytes).hexdigest(), original_filename='private.txt', mime_type='text/plain',
        size_bytes=len(private_bytes), source_type='upload', status='indexed'))
    db_session.commit()
    # Original source bytes remain pinned even after normal source removal.
    assert client.delete(f'/api/documents/{source.id}', headers=headers(world['author'])).status_code == 204
    task.description = 'Unpublished replacement'
    db_session.commit()
    payload = selection(client, world, version_id)
    response = client.post(BASE+'/export', headers=headers(world['owner']), json=payload)
    assert response.status_code == 200, response.text
    assert 'no-store' in response.headers['cache-control']
    with ZipFile(BytesIO(response.content)) as bundle:
        names = bundle.namelist()
        assert {'course.json', 'course.md', 'README.txt', 'manifest.json', 'SHA256SUMS'} <= set(names)
        assert all(not path.startswith('/') and '..' not in path for path in names)
        manifest = json.loads(bundle.read('manifest.json'))
        assert manifest['format_version'] == 1 and manifest['course_version_id'] == version_id
        assert not manifest['includes_answer_keys']
        for line in bundle.read('SHA256SUMS').decode().splitlines():
            digest, path = line.split('  ', 1)
            assert sha256(bundle.read(path)).hexdigest() == digest
        snapshot = json.loads(bundle.read('course.json'))
        assert 'correct_answers' not in json.dumps(snapshot)
        content = bundle.read('course.md').decode()
        assert 'Published exercise' in content and 'images/image-' in content
        assert 'PRIVATE EXEMPLAR' not in content and 'Unpublished replacement' not in content
        assert bundle.read(snapshot['sources'][0]['package_path']) == b'Approved original bytes'
        assert 'storage_key' not in bundle.read('manifest.json').decode()
        assert 'Private author generation prompt' not in content
        assert len([path for path in names if path.startswith('sources/')]) == 1
    event = db_session.query(AdminEvent).filter_by(action='learning.course_export_prepared').one()
    assert event.details['course_id'] == course.id and event.details['snapshot_sha256'] == payload['snapshot_sha256']
    assert 'answer' not in event.details and 'content' not in event.details


def test_export_requires_exact_owner_consent_and_private_keys_opt_in(client, db_session, world):
    course, _, _, _, _, version = publish(client, db_session, world['author'])
    foreign, *_ = publish(client, db_session, world['foreign'])
    operator = User(email='transfer-operator@test.invalid', password_hash='unused', is_admin=True)
    db_session.add(operator); db_session.commit()
    payload = selection(client, world, version.id)
    for actor in [world['admin'], world['author'], world['reviewer'], world['learner'], operator]:
        assert client.get(BASE, headers=headers(actor)).status_code == 403
        assert client.post(BASE+'/export', headers=headers(actor), json=payload).status_code == 403
    world['foreign'].organization_role = 'owner'; db_session.commit()
    assert client.post(BASE+'/export', headers=headers(world['foreign']), json=payload).status_code == 404
    assert client.post(BASE+'/export', headers=headers(world['owner']), json={**payload,'confirm_transfer':False}).status_code == 422
    assert client.post(BASE+'/export', headers=headers(world['owner']), json={**payload,'snapshot_sha256':'0'*64}).status_code == 409
    assert client.post(BASE+'/export', headers=headers(world['owner']), json={**payload,'include_answer_keys':True}).status_code == 422
    response = client.post(BASE+'/export', headers=headers(world['owner']), json={**payload,'include_answer_keys':True,'confirm_private_materials':True,'include_sources':False})
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as bundle:
        assert 'correct_answers' in bundle.read('course.json').decode()
        assert 'PRIVATE' in bundle.read('README.txt').decode()
        assert json.loads(bundle.read('manifest.json'))['includes_answer_keys']
    assert db_session.query(AdminEvent).filter_by(action='learning.course_export_prepared').count() == 1


@pytest.mark.parametrize('asset', ['image', 'source'])
def test_historical_foreign_reference_cannot_leak_into_transfer(client, db_session, world, monkeypatch, tmp_path, asset):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, _, _, _, _, version = publish(client, db_session, world['author'])
    foreign, *_ = publish(client, db_session, world['foreign'])
    row = db_session.get(PublishedLearningContent, version.id)
    snapshot = deepcopy(row.snapshot)
    if asset == 'image':
        image = CourseImage(course_id=foreign.id, owner_id=world['foreign'].id, status='approved', storage_key='foreign.png')
        db_session.add(image); db_session.flush()
        snapshot['modules'][0]['lessons'][0]['content'] = f'![private](/images/{image.id}/content)'
    else:
        source = document(db_session, foreign, world['foreign'])
        snapshot['sources'] = [{'document_id':source.id,'version':source.version,'content_hash':source.content_hash,'filename':source.original_filename,'mime_type':source.mime_type,'size_bytes':source.size_bytes}]
        db_session.add(PublishedSourceFile(course_version_id=version.id,document_id=source.id,document_version=source.version,content_hash=source.content_hash,storage_key=source.storage_key,filename=source.original_filename,mime_type=source.mime_type,size_bytes=source.size_bytes))
    row.snapshot = snapshot; db_session.commit()
    payload = selection(client, world, version.id)
    assert client.post(BASE+'/export', headers=headers(world['owner']), json=payload).status_code == 409
    assert db_session.query(AdminEvent).filter_by(action='learning.course_export_prepared').count() == 0


def test_missing_original_fails_closed_without_partial_archive_or_success_audit(client, db_session, world, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, *_ = publish(client, db_session, world['author'])
    source = document(db_session, course, world['author']); source.learner_visible = True
    course.content_revision += 1; db_session.commit()
    assert client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author'])).status_code == 200
    version = db_session.query(PublishedLearningContent).order_by(PublishedLearningContent.course_version_id.desc()).first()
    (tmp_path/source.storage_key).unlink()
    payload = selection(client, world, version.course_version_id)
    assert client.post(BASE+'/export', headers=headers(world['owner']), json=payload).status_code == 503
    assert db_session.query(AdminEvent).filter_by(action='learning.course_export_prepared').count() == 0


def test_package_size_limit_fails_before_export_receipt(client, db_session, world, monkeypatch):
    from app.services import course_transfer
    _, _, _, _, _, version = publish(client, db_session, world['author'])
    payload = selection(client, world, version.id)
    monkeypatch.setattr(course_transfer, 'MAX_BUNDLE_BYTES', 20)
    response = client.post(BASE+'/export', headers=headers(world['owner']), json=payload)
    assert response.status_code == 413
    assert response.headers.get('content-type', '').startswith('application/json')
    assert db_session.query(AdminEvent).filter_by(action='learning.course_export_prepared').count() == 0
