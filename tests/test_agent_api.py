from app.models.theory import Theory
from app.services import agent_service
from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_lesson, make_module


def test_agent_improves_theory_for_owner(client, db_session, monkeypatch):
    owner, token = register_and_login(
        client,
        db_session,
        email="agent-owner@example.com",
        password="secret123",
        full_name="Agent Owner",
    )
    course = make_course(db_session, owner_id=owner.id, name="Agent Course")
    module = make_module(db_session, course_id=course.id, title="Agent Module")
    lesson = make_lesson(db_session, module_id=module.id, title="Agent Lesson")
    db_session.add(Theory(lesson_id=lesson.id, content="Original theory"))
    db_session.commit()

    monkeypatch.setattr(agent_service, "aggregated_search", lambda query: ["External source"])
    monkeypatch.setattr(
        agent_service,
        "generate_from_prompt",
        lambda *args, **kwargs: {"improved_theory": "Improved theory"},
    )

    response = client.post(
        "/api/agent/improve-theory",
        json={"lesson_id": lesson.id, "goal": "Make it clearer"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["original"] == "Original theory"
    assert response.json()["improved"] == "Improved theory"
    assert response.json()["used_external_context"] is True


def test_agent_rejects_foreign_user(client, db_session):
    owner, _owner_token = register_and_login(
        client,
        db_session,
        email="agent-owner-2@example.com",
        password="secret123",
        full_name="Agent Owner 2",
    )
    _, outsider_token = register_and_login(
        client,
        db_session,
        email="agent-outsider@example.com",
        password="secret123",
        full_name="Agent Outsider",
    )
    course = make_course(db_session, owner_id=owner.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    db_session.add(Theory(lesson_id=lesson.id, content="Original theory"))
    db_session.commit()

    response = client.post(
        "/api/agent/improve-theory",
        json={"lesson_id": lesson.id, "goal": "Change it"},
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 403
