import json

from app.models.lesson import Lesson
from app.models.lesson_version import LessonVersion
from app.models.module import Module
from app.models.module_version import ModuleVersion
from app.models.task import Task
from app.models.test import Test as TestModel
from app.models.test_version import TestVersion as TestVersionModel
from app.models.theory import Theory
from app.models.user import User
from tests.factories import make_course


def _module(client, course_id, headers, title="M"):
    response = client.post(f"/api/courses/{course_id}/modules/", json={"title": title}, headers=headers)
    assert response.status_code == 201
    return response.json()


def _lesson(client, course_id, module_id, headers, title="L"):
    response = client.post(f"/api/courses/{course_id}/modules/{module_id}/lessons/", json={"title": title, "description": "d", "content": "content"}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_lesson_edit_revision_snapshot_and_stale_conflict(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = _module(client, course.id, auth_headers)
    lesson = _lesson(client, course.id, module["id"], auth_headers)
    response = client.put(f"/api/lessons/{lesson['id']}", json={"content": "changed", "expected_revision": 1}, headers=auth_headers)
    assert response.status_code == 200
    assert (response.json()["content"], response.json()["revision"]) == ("changed", 2)
    stale = client.put(f"/api/lessons/{lesson['id']}", json={"title": "lost", "expected_revision": 1}, headers=auth_headers)
    assert stale.status_code == 409
    db_session.expire_all()
    stored = db_session.get(Lesson, lesson["id"])
    assert stored.theory.content == "changed"
    versions = db_session.query(LessonVersion).filter_by(lesson_id=lesson["id"]).order_by(LessonVersion.revision).all()
    assert [item.revision for item in versions] == [1, 2]


def test_structured_and_legacy_test_editor(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = _module(client, course.id, auth_headers)
    structured = client.post(f"/api/modules/{module['id']}/tests/", json={"question": "Q", "answers": ["A", "B"], "correct_answer": "A"}, headers=auth_headers)
    assert structured.status_code == 201
    item = structured.json(); assert (item["position"], item["revision"]) == (0, 1)
    updated = client.put(f"/api/tests/{item['id']}", json={"question": "Q2", "answers": ["C"], "correct_answer": "C", "expected_revision": 1}, headers=auth_headers)
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    legacy = client.post(f"/api/modules/{module['id']}/tests/", json={"test": "Legacy", "description": "Варианты: Да, Нет (Правильный: Да)"}, headers=auth_headers)
    assert legacy.status_code == 201 and legacy.json()["position"] == 1
    final = client.post(f"/api/courses/{course.id}/tests/", json={"question": "Final", "answers": [], "correct_answer": ""}, headers=auth_headers)
    assert final.status_code == 201 and final.json()["assessment_scope"] == "final"
    assert db_session.query(TestVersionModel).filter_by(test_id=item["id"]).count() == 2


def test_batch_reorder_is_atomic_and_revision_aware(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    first = _module(client, course.id, auth_headers, "First")
    second = _module(client, course.id, auth_headers, "Second")
    payload = {"items": [{"id": first["id"], "position": 1, "expected_revision": 1}, {"id": second["id"], "position": 0, "expected_revision": 1}]}
    response = client.put(f"/api/courses/{course.id}/modules/order", json=payload, headers=auth_headers)
    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["Second", "First"]
    stale = client.put(f"/api/courses/{course.id}/modules/order", json=payload, headers=auth_headers)
    assert stale.status_code == 409
    db_session.expire_all()
    assert [(x.title, x.position, x.revision) for x in db_session.query(Module).filter_by(course_id=course.id).order_by(Module.position)] == [("Second", 0, 2), ("First", 1, 2)]


def test_module_delete_cascades_soft_delete_and_metrics(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = _module(client, course.id, auth_headers)
    lesson = _lesson(client, course.id, module["id"], auth_headers)
    question = client.post(f"/api/modules/{module['id']}/tests/", json={"question": "Q", "answers": [], "correct_answer": ""}, headers=auth_headers).json()
    db_session.add(Task(module_id=module["id"], name="Task")); db_session.commit()
    response = client.delete(f"/api/modules/{module['id']}", params={"expected_revision": 1}, headers=auth_headers)
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Module, module["id"]).is_deleted
    assert db_session.get(Lesson, lesson["id"]).is_deleted
    assert db_session.query(Theory).filter_by(lesson_id=lesson["id"]).one().is_deleted
    assert db_session.get(TestModel, question["id"]).is_deleted
    assert db_session.query(Task).filter_by(module_id=module["id"]).one().is_deleted
    structure = client.get(f"/api/courses/{course.id}/structure", headers=auth_headers).json()
    assert structure["metrics"]["module_count"] == 0


def test_editor_and_versions_tenant_isolation(client, db_session, auth_user, auth_headers):
    foreign = User(email="editor-foreign@example.com", password_hash="hash")
    db_session.add(foreign); db_session.commit()
    course = make_course(db_session, owner_id=foreign.id)
    module = Module(course_id=course.id, title="Secret", position=0, revision=1)
    db_session.add(module); db_session.commit()
    assert client.put(f"/api/modules/{module.id}", json={"title": "x", "expected_revision": 1}, headers=auth_headers).status_code == 404
    assert client.get(f"/api/versions/module/{module.id}", headers=auth_headers).status_code == 404


def test_mutations_blocked_while_generating(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = _module(client, course.id, auth_headers)
    course.status = "generating"; db_session.commit()
    response = client.put(f"/api/modules/{module['id']}", json={"title": "x", "expected_revision": 1}, headers=auth_headers)
    assert response.status_code == 409
