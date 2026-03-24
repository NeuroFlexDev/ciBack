from app.models.theory import Theory
from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_lesson, make_module


def test_feedback_create_and_manager_list(client, db_session):
    owner, owner_token = register_and_login(
        client,
        db_session,
        email="feedback-owner@example.com",
        password="secret123",
        full_name="Feedback Owner",
    )
    _, student_token = register_and_login(
        client,
        db_session,
        email="feedback-student@example.com",
        password="secret123",
        full_name="Feedback Student",
    )
    course = make_course(db_session, owner_id=owner.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    db_session.add(Theory(lesson_id=lesson.id, content="Theory"))
    db_session.commit()

    create_response = client.post(
        "/api/feedback/",
        json={"lesson_id": lesson.id, "type": "general", "comment": "Helpful", "rating": 4},
        headers=auth_headers(student_token),
    )
    assert create_response.status_code == 200
    assert create_response.json()["author_id"] is not None

    list_response = client.get(
        f"/api/feedback/lesson/{lesson.id}",
        headers=auth_headers(owner_token),
    )
    assert list_response.status_code == 200
    assert list_response.json()[0]["comment"] == "Helpful"


def test_feedback_list_forbidden_for_foreign_user(client, db_session):
    owner, _owner_token = register_and_login(
        client,
        db_session,
        email="feedback-owner-2@example.com",
        password="secret123",
        full_name="Feedback Owner 2",
    )
    _, outsider_token = register_and_login(
        client,
        db_session,
        email="feedback-outsider@example.com",
        password="secret123",
        full_name="Feedback Outsider",
    )
    course = make_course(db_session, owner_id=owner.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    db_session.add(Theory(lesson_id=lesson.id, content="Theory"))
    db_session.commit()

    response = client.get(
        f"/api/feedback/lesson/{lesson.id}",
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 403
