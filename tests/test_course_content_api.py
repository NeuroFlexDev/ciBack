from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.core.security import create_access_token, hash_password
from app.models.course_graph import CourseGraph
from app.models.document import Document
from app.models.user import User
from app.repositories.course_content import CourseContentRepository
from app.services.course_content_service import CourseContentService
from app.services.file_storage import LocalFileStorage, get_file_storage
from main import app
from tests.factories import make_course


def _storage_override(tmp_path: Path, max_bytes: int = 50 * 1024 * 1024):
    storage = LocalFileStorage(tmp_path, max_bytes)
    app.dependency_overrides[get_file_storage] = lambda: storage
    return storage


def _other_headers(db_session):
    user = User(
        email="other@example.com",
        password_hash=hash_password("password123"),
    )
    db_session.add(user)
    db_session.commit()
    return user, {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_canvas_empty_versioned_and_conflict(
    client, db_session, auth_user, auth_headers
):
    course = make_course(db_session, owner_id=auth_user.id)

    response = client.get(
        f"/api/courses/{course.id}/canvas", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == {
        "course_id": course.id,
        "graph_id": None,
        "version": 0,
        "nodes": [],
        "edges": [],
        "status": None,
        "created_by": None,
        "created_at": None,
        "updated_at": None,
    }

    first_payload = {
        "version": 0,
        "nodes": [
            {"id": "a", "position": {"x": 10, "y": 20}},
            {"id": "b", "data": {"label": "B"}},
        ],
        "edges": [{"id": "a-b", "source": "a", "target": "b"}],
    }
    response = client.put(
        f"/api/courses/{course.id}/canvas",
        json=first_payload,
        headers=auth_headers,
    )
    assert response.status_code == 200
    first = response.json()
    assert first["version"] == 1
    assert first["nodes"][0]["position"] == {"x": 10, "y": 20}

    stale = client.put(
        f"/api/courses/{course.id}/canvas",
        json=first_payload,
        headers=auth_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 1

    second = client.put(
        f"/api/courses/{course.id}/canvas",
        json={"version": 1, "nodes": [{"id": "c"}], "edges": []},
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2

    graphs = (
        db_session.query(CourseGraph)
        .filter(CourseGraph.course_id == course.id)
        .order_by(CourseGraph.version)
        .all()
    )
    assert graphs[0].nodes == first_payload["nodes"]
    assert graphs[0].status == "archived"
    assert graphs[1].status == "published"


def test_canvas_validation_access_and_auth(
    client, db_session, auth_user, auth_headers
):
    course = make_course(db_session, owner_id=auth_user.id)
    other, other_headers = _other_headers(db_session)

    invalid = client.put(
        f"/api/courses/{course.id}/canvas",
        json={
            "version": 0,
            "nodes": [{"id": "a"}],
            "edges": [{"source": "a", "target": "missing"}],
        },
        headers=auth_headers,
    )
    assert invalid.status_code == 422
    assert client.get(f"/api/courses/{course.id}/canvas").status_code == 401
    assert (
        client.get(
            f"/api/courses/{course.id}/canvas", headers=other_headers
        ).status_code
        == 404
    )


def test_document_upload_list_filters_and_storage(
    client, db_session, auth_user, auth_headers, tmp_path
):
    storage = _storage_override(tmp_path)
    course = make_course(db_session, owner_id=auth_user.id)

    response = client.post(
        f"/api/courses/{course.id}/documents",
        files={"file": ("notes.txt", b"hello course", "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["original_filename"] == "notes.txt"
    assert "storage_key" not in body
    assert "content_hash" not in body

    document = db_session.get(Document, body["id"])
    assert document.content_hash
    assert (storage.root / document.storage_key).read_bytes() == b"hello course"

    client.post(
        f"/api/courses/{course.id}/documents",
        files={"file": ("guide.pdf", b"%PDF-test", "application/pdf")},
        headers=auth_headers,
    )
    listed = client.get(
        f"/api/courses/{course.id}/documents",
        params={
            "limit": 1,
            "offset": 0,
            "status": "uploaded",
            "source_type": "upload",
            "sort_by": "original_filename",
            "sort_order": "asc",
        },
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert listed.json()["limit"] == 1
    assert listed.json()["items"][0]["original_filename"] == "guide.pdf"


def test_document_upload_rejections_and_isolation(
    client, db_session, auth_user, auth_headers, tmp_path
):
    _storage_override(tmp_path, max_bytes=4)
    course = make_course(db_session, owner_id=auth_user.id)
    _, other_headers = _other_headers(db_session)

    unsupported = client.post(
        f"/api/courses/{course.id}/documents",
        files={"file": ("image.png", b"png", "image/png")},
        headers=auth_headers,
    )
    assert unsupported.status_code == 415

    oversized = client.post(
        f"/api/courses/{course.id}/documents",
        files={"file": ("large.txt", b"12345", "text/plain")},
        headers=auth_headers,
    )
    assert oversized.status_code == 413
    assert not list(tmp_path.rglob("*.txt"))

    empty = client.post(
        f"/api/courses/{course.id}/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
        headers=auth_headers,
    )
    assert empty.status_code == 400

    assert (
        client.get(
            f"/api/courses/{course.id}/documents", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/courses/{course.id}/documents",
            files={"file": ("notes.txt", b"ok", "text/plain")},
            headers=other_headers,
        ).status_code
        == 404
    )


def test_storage_is_cleaned_when_document_commit_fails(
    db_session, auth_user, tmp_path, monkeypatch
):
    storage = LocalFileStorage(tmp_path, 1024)
    course = make_course(db_session, owner_id=auth_user.id)

    def fail_add(*args, **kwargs):
        raise RuntimeError("database failure")

    monkeypatch.setattr(CourseContentRepository, "add_document", fail_add)
    upload = UploadFile(
        filename="notes.txt",
        file=BytesIO(b"content"),
        headers={"content-type": "text/plain"},
    )

    with pytest.raises(RuntimeError, match="database failure"):
        CourseContentService.upload_document(
            db_session,
            course_id=course.id,
            owner_id=auth_user.id,
            upload=upload,
            storage=storage,
        )

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_local_storage_rejects_path_escape(tmp_path):
    storage = LocalFileStorage(tmp_path, 1024)

    with pytest.raises(ValueError, match="invalid storage key"):
        storage.save(BytesIO(b"content"), "../outside.txt")
