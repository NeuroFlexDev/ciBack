from io import BytesIO
from sqlalchemy.exc import IntegrityError
import pytest
from app.core.config import settings
from app.models.document import Document
from app.models.learning import LearningAssignment, PublishedLearningContent
from app.models.materials import PublishedSourceFile
from app.services.file_storage import get_file_storage
from tests.test_learning_runtime import world, publish, assign, headers, grant


def document(db, course, author, text=b"Approved learner source"):
    storage = get_file_storage()
    stored = storage.save(BytesIO(text), f"test/{course.id}/source.txt")
    row = Document(owner_id=author.id, course_id=course.id, storage_key=f"test/{course.id}/source.txt",
        content_hash=stored.content_hash, original_filename="Источник.txt", mime_type="text/plain", size_bytes=stored.size_bytes,
        source_type="upload", status="indexed")
    db.add(row); db.commit()
    return row


def test_release_requires_explicit_review_and_pins_assigned_original(client, db_session, world, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    c, _, _, _, _, v = publish(client, db_session, world['author'], assessment=False, practice=False)
    source = document(db_session, c, world['author'])
    old = assign(client, world, c, target=world['second'])
    base = f"/api/courses/{c.id}/source-release"
    h = headers(world['author'])
    response = client.get(base, headers=h)
    assert not response.json()['items'][0]['learner_visible']
    payload = {'learner_visible': True, 'expected_revision': response.json()['content_revision'],
        'document_version': source.version, 'content_hash': source.content_hash}
    assert client.put(f"{base}/{source.id}", headers=h, json=payload).status_code == 422
    payload['confirm_original_safe'] = True
    assert client.put(f"{base}/{source.id}", headers=headers(world['foreign']), json=payload).status_code == 404
    assert client.put(f"{base}/{source.id}", headers=h, json=payload).status_code == 200
    assert client.put(f"{base}/{source.id}", headers=h, json=payload).status_code == 409
    assert client.post(f'/api/courses/{c.id}/publish', headers=h).status_code == 200
    current = assign(client, world, c)
    path = f"/api/learning/assignments/{current['id']}/sources/{source.id}/content"
    snapshot = client.get(f"/api/learning/assignments/{current['id']}", headers=headers(world['learner'])).json()['content']
    assert snapshot['sources'][0]['content_hash'] == source.content_hash
    assert 'storage_key' not in snapshot['sources'][0]
    assert client.get(path, headers=headers(world['learner'])).content == b"Approved learner source"
    assert client.get(path, headers=headers(world['second'])).status_code == 404
    assert client.get(f"/api/learning/assignments/{old['id']}/sources/{source.id}/content", headers=headers(world['second'])).status_code == 404
    assert db_session.query(PublishedSourceFile).count() == 1
    # Source removal changes future releases, never historical assigned bytes.
    assert client.delete(f'/api/documents/{source.id}', headers=h).status_code == 204
    assert client.post(f'/api/courses/{c.id}/publish', headers=h).status_code == 200
    assert client.get(path, headers=headers(world['learner'])).content == b"Approved learner source"
    latest = db_session.query(PublishedLearningContent).order_by(PublishedLearningContent.course_version_id.desc()).first()
    assert latest.snapshot['sources'] == []
    # An accidental storage replacement is detected, not served as the old file.
    (tmp_path / source.storage_key).write_bytes(b"tampered")
    assert client.get(path, headers=headers(world['learner'])).status_code == 503
    assignment = db_session.get(LearningAssignment, current['id'])
    assignment.state = 'revoked'; db_session.commit()
    assert client.get(path, headers=headers(world['learner'])).status_code == 403


def test_scoped_reviewer_can_check_original_but_cannot_release(client, db_session, world, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    c, _, _, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    source = document(db_session, c, world['author'])
    grant(client, world, 'reviewer', 'reviewer', 'course', c.id)
    base = f'/api/courses/{c.id}/source-release'
    data = client.get(base, headers=headers(world['reviewer'])).json()
    assert not data['can_edit']
    result = client.get(data['items'][0]['content_url'], headers=headers(world['reviewer']))
    assert result.status_code == 200 and result.headers['content-disposition'].startswith('attachment;')
    payload = {'learner_visible': True, 'confirm_original_safe': True, 'expected_revision': data['content_revision'],
        'document_version': source.version, 'content_hash': source.content_hash}
    assert client.put(f'{base}/{source.id}', headers=headers(world['reviewer']), json=payload).status_code == 404
