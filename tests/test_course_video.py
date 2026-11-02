from io import BytesIO
import json
import subprocess
from zipfile import ZipFile

import pytest
from app.core.config import settings
from app.models.materials import CourseImage
from app.models.theory import Theory
from tests.factories import make_course
from tests.test_learning_runtime import world, publish, headers, assign
from tests.test_course_transfer import selection, BASE


@pytest.fixture(scope='module', params=['mp4', 'webm'])
def video(request, tmp_path_factory):
    fmt = request.param
    path = tmp_path_factory.mktemp('media-fixture') / f'video.{fmt}'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=32x24:r=10',
        '-t', '0.3', '-c:v', 'libx264' if fmt == 'mp4' else 'libvpx-vp9', '-threads', '1',
        '-pix_fmt', 'yuv420p', str(path)], check=True, timeout=20)
    return path.name, path.read_bytes(), f'video/{fmt}'


def test_upload_probe_range_private_access_and_library(client, db_session, auth_user, auth_headers, tmp_path, monkeypatch, video):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course = make_course(db_session, owner_id=auth_user.id)
    response = client.post(f'/api/courses/{course.id}/media', headers=auth_headers, files={'file': video})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item['mime_type'] == video[2] and item['original_filename'] == video[0]
    assert item['width'] == 32 and item['height'] == 24 and item['duration_seconds'] > 0
    assert item['status'] == 'pending_review'
    assert client.get(item['content_url']).status_code == 401
    fetched = client.get(item['content_url'], headers=auth_headers)
    assert fetched.content == video[1] and 'no-store' in fetched.headers['cache-control']
    part = client.get(item['content_url'], headers={**auth_headers, 'Range': 'bytes=4-11'})
    assert part.status_code == 206 and part.content == video[1][4:12]
    assert client.get(f'/api/courses/{course.id}/media', headers=auth_headers).json()['items'][0]['id'] == item['id']
    assert client.get(f'/api/courses/{course.id}/images', headers=auth_headers).json()['items'] == []


def test_invalid_oversized_and_over_quota_uploads_leave_no_files(client, db_session, auth_user, auth_headers, tmp_path, monkeypatch, video):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course = make_course(db_session, owner_id=auth_user.id)
    path = f'/api/courses/{course.id}/media'
    for fake in [b'<svg onload="alert(1)"/>', b'#EXTM3U\nfile:///etc/passwd', b'\0\0\0\x18ftypisomtruncated']:
        assert client.post(path, headers=auth_headers, files={'file': ('fake.mp4', fake, 'video/mp4')}).status_code in {415, 422}
    monkeypatch.setattr(settings, 'VIDEO_MAX_BYTES', 10)
    assert client.post(path, headers=auth_headers, files={'file': video}).status_code == 413
    monkeypatch.setattr(settings, 'VIDEO_MAX_BYTES', 1024 * 1024)
    monkeypatch.setattr(settings, 'VIDEO_MAX_PIXELS', 1)
    assert client.post(path, headers=auth_headers, files={'file': video}).status_code == 413
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]
    assert db_session.query(CourseImage).count() == 0
    monkeypatch.setattr(settings, 'VIDEO_MAX_PIXELS', 3840 * 2160)
    monkeypatch.setattr('app.services.material_quota.material_policy', lambda db, user: {'disk_bytes_limit': 1})
    assert client.post(path, headers=auth_headers, files={'file': video}).status_code == 413
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]


def test_publication_pins_video_export_and_assignment_revocation(client, db_session, world, tmp_path, monkeypatch, video):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, _, lesson, _, _, _ = publish(client, db_session, world['author'])
    item_response = client.post(f'/api/courses/{course.id}/media', headers=headers(world['author']), files={'file': video})
    assert item_response.status_code == 201, item_response.text
    item = item_response.json()
    assert client.get(item['content_url'], headers=headers(world['foreign'])).status_code == 404
    assert client.get(item['content_url'], headers=headers(world['learner'])).status_code == 404
    theory = db_session.query(Theory).filter_by(lesson_id=lesson.id).one()
    theory.content = f"# Учебное видео\n\n![Инструкция]({item['content_url']})"
    course.content_revision += 1
    db_session.commit()
    path = f'/api/courses/{course.id}/publish'
    assert client.post(path, headers=headers(world['author'])).status_code == 409
    assert client.post(f"/api/images/{item['id']}/review", headers=headers(world['author']), json={'decision': 'approved'}).status_code == 200
    published = client.post(path, headers=headers(world['author']))
    assert published.status_code == 200, published.text
    assignment = assign(client, world, course)
    assert client.get(item['content_url'], headers=headers(world['learner'])).content == video[1]
    # Editing today's draft does not remove access from the pinned assignment.
    theory.content = 'Новая редакция без видео'
    db_session.commit()
    assert client.get(item['content_url'], headers=headers(world['learner'])).status_code == 200
    payload = selection(client, world, assignment['course_version_id'])
    exported = client.post(BASE + '/export', headers=headers(world['owner']), json=payload)
    assert exported.status_code == 200, exported.text
    with ZipFile(BytesIO(exported.content)) as archive:
        file = next(name for name in archive.namelist() if name.startswith('videos/'))
        assert archive.read(file) == video[1]
        assert '[Видео / Video: Инструкция]' in archive.read('course.md').decode()
        snapshot = json.loads(archive.read('course.json'))
        assert snapshot['media'][0]['type'] == 'video'
    pdf = client.post(BASE + '/export.pdf', headers=headers(world['owner']), json=payload)
    assert pdf.status_code == 200, pdf.text[:200] if pdf.status_code != 200 else ''
    from pypdf import PdfReader
    assert 'просмотр доступен в Лерниуме' in ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert client.delete(f"/api/learning/assignments/{assignment['id']}", headers=headers(world['owner'])).status_code == 200
    assert client.get(item['content_url'], headers=headers(world['learner'])).status_code == 404
