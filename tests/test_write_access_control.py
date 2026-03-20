import io
import json

from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_cs, make_lesson, make_module


def test_anonymous_write_requests_are_rejected(client):
    response = client.post(
        "/api/courses/",
        json={
            "title": "Protected course",
            "description": "desc",
            "level": 1,
            "language": 1,
        },
    )
    assert response.status_code == 401


def test_only_owner_or_editor_can_modify_course(client, db_session):
    owner, owner_token = register_and_login(
        client,
        db_session,
        email="owner@example.com",
        password="secret123",
        full_name="Owner User",
    )
    _, outsider_token = register_and_login(
        client,
        db_session,
        email="outsider@example.com",
        password="secret123",
        full_name="Outsider User",
    )
    _, editor_token = register_and_login(
        client,
        db_session,
        email="editor-access@example.com",
        password="secret123",
        full_name="Editor User",
        role="editor",
    )

    create_response = client.post(
        "/api/courses/",
        json={
            "title": "Owned course",
            "description": "desc",
            "level": 1,
            "language": 1,
        },
        headers=auth_headers(owner_token),
    )
    assert create_response.status_code == 200
    course_id = create_response.json()["id"]

    forbidden_update = client.put(
        f"/api/courses/{course_id}",
        json={"title": "hijacked"},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_update.status_code == 403

    editor_update = client.put(
        f"/api/courses/{course_id}",
        json={"title": "editor-updated"},
        headers=auth_headers(editor_token),
    )
    assert editor_update.status_code == 200
    assert editor_update.json()["title"] == "editor-updated"

    owner_delete = client.delete(f"/api/courses/{course_id}", headers=auth_headers(owner_token))
    assert owner_delete.status_code == 200


def test_foreign_user_cannot_modify_owned_course_tree(client, db_session, monkeypatch):
    owner, owner_token = register_and_login(
        client,
        db_session,
        email="tree-owner@example.com",
        password="secret123",
        full_name="Tree Owner",
    )
    _, outsider_token = register_and_login(
        client,
        db_session,
        email="tree-outsider@example.com",
        password="secret123",
        full_name="Tree Outsider",
    )
    course = make_course(db_session, owner_id=owner.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    structure = make_cs(db_session, course_id=course.id)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.upload_service.generate_from_prompt",
        lambda *args, **kwargs: {"summary": "Updated from document"},
    )

    forbidden_module_create = client.post(
        f"/api/courses/{course.id}/modules/",
        json={"course_id": course.id, "title": "Foreign module"},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_module_create.status_code == 403

    forbidden_lesson_update = client.put(
        f"/api/lessons/{lesson.id}",
        json={"title": "Foreign lesson"},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_lesson_update.status_code == 403

    forbidden_theory_create = client.post(
        "/api/theories/",
        json={"lesson_id": lesson.id, "content": "Foreign theory"},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_theory_create.status_code == 403

    forbidden_task_create = client.post(
        f"/api/modules/{module.id}/tasks/",
        json={"name": "Foreign task", "description": "x"},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_task_create.status_code == 403

    forbidden_test_create = client.post(
        f"/api/modules/{module.id}/tests/",
        json={"question": "Q", "answers": ["A"], "correct": "A"},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_test_create.status_code == 403

    forbidden_upload = client.post(
        f"/api/courses/{course.id}/upload-description",
        files={"file": ("course.txt", io.BytesIO(b"foreign upload"), "text/plain")},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_upload.status_code == 403

    forbidden_generation = client.get(
        f"/api/courses/{course.id}/generate_modules",
        params={"cs_id": structure.id},
        headers=auth_headers(outsider_token),
    )
    assert forbidden_generation.status_code == 403

    owner_module_create = client.post(
        f"/api/courses/{course.id}/modules/",
        json={"course_id": course.id, "title": "Owner module"},
        headers=auth_headers(owner_token),
    )
    assert owner_module_create.status_code == 200


def test_course_structure_write_requires_privileged_role(client, db_session):
    _, student_token = register_and_login(
        client,
        db_session,
        email="student-structure@example.com",
        password="secret123",
        full_name="Student User",
        role="student",
    )
    _, lnd_token = register_and_login(
        client,
        db_session,
        email="lnd-structure@example.com",
        password="secret123",
        full_name="LND User",
        role="lnd",
    )

    payload = {
        "sections": 2,
        "tests_per_section": 1,
        "lessons_per_section": 2,
        "questions_per_test": 5,
        "final_test": True,
        "content_types": ["theory"],
    }

    forbidden_create = client.post(
        "/api/course-structure/",
        json=payload,
        headers=auth_headers(student_token),
    )
    assert forbidden_create.status_code == 403

    allowed_create = client.post(
        "/api/course-structure/",
        json=payload,
        headers=auth_headers(lnd_token),
    )
    assert allowed_create.status_code == 200
