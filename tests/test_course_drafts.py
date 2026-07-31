from app.models.course import Course
from app.models.domain_enums import CourseStatus
from app.models.user import User
from app.core.security import create_access_token, hash_password
from app.models.document import Document
from app.services.file_storage import LocalFileStorage, get_file_storage
from main import app
import pytest
from sqlalchemy.exc import IntegrityError


def test_create_and_list_course_draft(client, db_session, auth_user, auth_headers):
    response = client.post("/api/courses/drafts", headers=auth_headers)

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "draft"
    assert payload["setup_step"] == "documents"
    assert payload["title"] is None
    assert payload["created_at"]
    assert payload["updated_at"]

    draft = db_session.get(Course, payload["id"])
    assert draft.owner_id == auth_user.id
    assert draft.name is None
    assert draft.status == CourseStatus.DRAFT.value

    listed = client.get("/api/courses/drafts", headers=auth_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [draft.id]


def test_draft_requires_authentication(client):
    assert client.post("/api/courses/drafts").status_code == 401
    assert client.get("/api/courses/drafts").status_code == 401


def test_drafts_are_owner_isolated_and_excluded_from_regular_list(
    client, db_session, auth_user, auth_headers
):
    foreign_user = User(
        email="draft-owner-2@example.com",
        password_hash=hash_password("password123"),
    )
    db_session.add(foreign_user)
    db_session.commit()
    db_session.refresh(foreign_user)
    foreign_headers = {
        "Authorization": f"Bearer {create_access_token(foreign_user.id)}"
    }

    owned = client.post("/api/courses/drafts", headers=auth_headers).json()
    foreign = client.post("/api/courses/drafts", headers=foreign_headers).json()

    assert [item["id"] for item in client.get(
        "/api/courses/drafts", headers=auth_headers
    ).json()] == [owned["id"]]
    assert client.put(
        f"/api/courses/{foreign['id']}",
        json={"title": "No access"},
        headers=auth_headers,
    ).status_code == 404
    assert client.get("/api/courses/", headers=auth_headers).json() == []


def test_regular_course_is_ready_and_soft_deleted(
    client, db_session, auth_headers
):
    response = client.post(
        "/api/courses/",
        json={"title": "Ready", "description": "desc", "level": 1, "language": 1},
        headers=auth_headers,
    )
    course_id = response.json()["id"]
    course = db_session.get(Course, course_id)
    assert course.status == CourseStatus.READY.value

    assert client.delete(f"/api/courses/{course_id}", headers=auth_headers).status_code == 200
    db_session.refresh(course)
    assert course.is_deleted is True
    assert client.get("/api/courses/", headers=auth_headers).json() == []


def test_draft_id_accepts_document_without_starting_processing(
    client, db_session, auth_headers, tmp_path
):
    storage = LocalFileStorage(tmp_path, 1024)
    app.dependency_overrides[get_file_storage] = lambda: storage
    draft_id = client.post("/api/courses/drafts", headers=auth_headers).json()["id"]

    response = client.post(
        f"/api/courses/{draft_id}/documents",
        files={"file": ("source.txt", b"draft source", "text/plain")},
        headers=auth_headers,
    )

    assert response.status_code == 202
    document = db_session.get(Document, response.json()["id"])
    assert document.course_id == draft_id
    assert document.status == "uploaded"
    assert document.chunks == []


def test_database_rejects_null_title_for_non_draft(db_session, auth_user):
    db_session.add(
        Course(
            owner_id=auth_user.id,
            name=None,
            status=CourseStatus.CONFIGURED.value,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
