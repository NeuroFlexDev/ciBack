from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_lesson, make_module


def test_graph_returns_course_tree_for_owner(client, db_session):
    owner, token = register_and_login(
        client,
        db_session,
        email="graph-owner@example.com",
        password="secret123",
        full_name="Graph Owner",
    )
    course = make_course(db_session, owner_id=owner.id, name="Graph Course")
    module = make_module(db_session, course_id=course.id, title="Module A")
    lesson = make_lesson(db_session, module_id=module.id, title="Lesson A")
    db_session.commit()

    response = client.get(
        "/api/graph",
        params={"course_id": course.id},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["course_id"] == course.id
    assert any(node["label"] == "Graph Course" for node in body["nodes"])
    assert any(node["label"] == "Module A" for node in body["nodes"])
    assert any(node["label"] == "Lesson A" for node in body["nodes"])
    assert any(edge["relation"] == "contains" for edge in body["edges"])


def test_graph_rejects_foreign_user(client, db_session):
    owner, _owner_token = register_and_login(
        client,
        db_session,
        email="graph-owner-2@example.com",
        password="secret123",
        full_name="Graph Owner 2",
    )
    _, outsider_token = register_and_login(
        client,
        db_session,
        email="graph-outsider@example.com",
        password="secret123",
        full_name="Graph Outsider",
    )
    course = make_course(db_session, owner_id=owner.id)
    db_session.commit()

    response = client.get(
        "/api/graph",
        params={"course_id": course.id},
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 403
