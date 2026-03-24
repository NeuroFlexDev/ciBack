import json

import app.models.test as test_model_module
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.theory import Theory
from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_lesson, make_module


def test_versioning_snapshot_and_restore_course_tree(client, db_session):
    owner, token = register_and_login(
        client,
        db_session,
        email="versioning-owner@example.com",
        password="secret123",
        full_name="Versioning Owner",
    )
    course = make_course(
        db_session,
        owner_id=owner.id,
        name="Course v1",
        description="Original desc",
        level=1,
        language=1,
    )
    module = make_module(db_session, course_id=course.id, title="Module v1")
    lesson = make_lesson(db_session, module_id=module.id, title="Lesson v1")
    db_session.add(Theory(lesson_id=lesson.id, content="Theory v1"))
    db_session.add(Task(module_id=module.id, lesson_id=None, name="Module task v1", description="Task desc"))
    db_session.add(
        test_model_module.Test(
            module_id=module.id,
            lesson_id=lesson.id,
            question="Question v1",
            answers=json.dumps(["A", "B"]),
            correct_answer="A",
        )
    )
    db_session.commit()

    snapshot_response = client.post(
        f"/api/versions/course/{course.id}/snapshot",
        headers=auth_headers(token),
    )
    assert snapshot_response.status_code == 200
    course_version_id = snapshot_response.json()["id"]

    course_versions_response = client.get(f"/api/versions/course/{course.id}")
    assert course_versions_response.status_code == 200
    assert course_versions_response.json()[0]["id"] == course_version_id

    module_versions_response = client.get(f"/api/versions/course-version/{course_version_id}/modules")
    assert module_versions_response.status_code == 200
    module_version_id = module_versions_response.json()[0]["id"]

    lesson_versions_response = client.get(f"/api/versions/module-version/{module_version_id}/lessons")
    assert lesson_versions_response.status_code == 200
    assert lesson_versions_response.json()[0]["title"] == "Lesson v1"

    course.name = "Course mutated"
    module.title = "Module mutated"
    lesson.title = "Lesson mutated"
    lesson.theory.content = "Theory mutated"
    module.tasks[0].name = "Module task mutated"
    lesson.tests[0].question = "Question mutated"
    db_session.commit()

    restore_response = client.post(
        f"/api/versions/course-version/{course_version_id}/restore",
        headers=auth_headers(token),
    )
    assert restore_response.status_code == 200
    assert restore_response.json() == {
        "ok": True,
        "course_id": course.id,
        "course_version_id": course_version_id,
    }

    restored_course = db_session.query(Course).filter(Course.id == course.id).one()
    active_modules = [item for item in restored_course.modules if not item.is_deleted]
    assert restored_course.name == "Course v1"
    assert len(active_modules) == 1
    assert active_modules[0].title == "Module v1"

    active_lessons = [item for item in active_modules[0].lessons if not item.is_deleted]
    assert len(active_lessons) == 1
    assert active_lessons[0].title == "Lesson v1"
    assert active_lessons[0].theory.content == "Theory v1"
    assert [item.name for item in active_modules[0].tasks if not item.is_deleted] == ["Module task v1"]
    assert [item.question for item in active_lessons[0].tests if not item.is_deleted] == ["Question v1"]


def test_legacy_versioning_endpoints_fail_explicitly(client):
    module_legacy_response = client.get("/api/versions/module/1")
    assert module_legacy_response.status_code == 410

    lesson_legacy_response = client.get("/api/versions/lesson/1")
    assert lesson_legacy_response.status_code == 410
