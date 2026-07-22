from app.models.feedback import Feedback
from app.models.theory import Theory
from tests.factories import make_course, make_lesson, make_module


def test_connected_routes_require_authentication(client):
    requests = [
        ("get", "/api/chat/models", {}),
        ("get", "/api/graph", {"params": {"course_id": 1}}),
        ("get", "/api/search", {"params": {"q": "test"}}),
        ("get", "/api/agent/improve-theory", {"params": {"lesson_id": 1, "goal": "test"}}),
        ("post", "/api/feedback/", {"json": {"lesson_id": 1}}),
    ]

    for method, url, kwargs in requests:
        assert getattr(client, method)(url, **kwargs).status_code == 401


def test_chat_router_is_available(client, auth_headers):
    response = client.post("/api/chat/", json={"name": "Audit chat"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["name"] == "Audit chat"


def test_graph_only_exposes_owned_course(client, db_session, auth_user, auth_headers):
    owned = make_course(db_session, owner_id=auth_user.id)
    foreign = make_course(db_session, owner_id=None)

    assert client.get("/api/graph", params={"course_id": owned.id}, headers=auth_headers).status_code == 200
    assert client.get("/api/graph", params={"course_id": foreign.id}, headers=auth_headers).status_code == 404


def test_search_receives_only_owned_lesson_ids(
    client, db_session, auth_user, auth_headers, monkeypatch
):
    course = make_course(db_session, owner_id=auth_user.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    captured = {}

    def fake_search(query, k=5, allowed_lesson_ids=None):
        captured["query"] = query
        captured["lesson_ids"] = allowed_lesson_ids
        return []

    monkeypatch.setattr("app.routes.search.search", fake_search)
    response = client.get("/api/search", params={"q": "owned"}, headers=auth_headers)

    assert response.status_code == 200
    assert captured == {"query": "owned", "lesson_ids": {lesson.id}}


def test_feedback_sets_authenticated_author(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)

    response = client.post(
        "/api/feedback/",
        json={"lesson_id": lesson.id, "comment": "Useful", "rating": 5},
        headers=auth_headers,
    )

    assert response.status_code == 200
    feedback = db_session.query(Feedback).one()
    assert feedback.author_id == auth_user.id


def test_feedback_rejects_foreign_lesson(client, db_session, auth_headers):
    course = make_course(db_session, owner_id=None)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)

    response = client.post(
        "/api/feedback/",
        json={"lesson_id": lesson.id, "comment": "Hidden", "rating": 1},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert db_session.query(Feedback).count() == 0


def test_agent_router_returns_not_found_without_calling_external_services(
    client, auth_headers
):
    response = client.get(
        "/api/agent/improve-theory",
        params={"lesson_id": 999999, "goal": "clarify"},
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_agent_router_invokes_service_for_owned_lesson(
    client, db_session, auth_user, auth_headers, monkeypatch
):
    course = make_course(db_session, owner_id=auth_user.id)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    db_session.add(Theory(lesson_id=lesson.id, content="Original"))
    db_session.commit()

    monkeypatch.setattr(
        "app.routes.agent.get_course_agent",
        lambda db: {"improve_theory": lambda lesson_id, goal: f"{lesson_id}:{goal}"},
    )
    response = client.get(
        "/api/agent/improve-theory",
        params={"lesson_id": lesson.id, "goal": "clarify"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"original": "Original", "improved": f"{lesson.id}:clarify"}
