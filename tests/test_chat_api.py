from app.services import chat_service
from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course


def test_chat_api_crud_and_send(client, db_session, monkeypatch):
    user, token = register_and_login(
        client,
        db_session,
        email="chat-api@example.com",
        password="secret123",
        full_name="Chat API User",
    )
    course = make_course(db_session, owner_id=user.id)
    db_session.commit()

    create_response = client.post(
        "/api/chat/",
        json={"name": "Pilot chat", "course_id": course.id},
        headers=auth_headers(token),
    )
    assert create_response.status_code == 200
    chat_id = create_response.json()["id"]
    assert create_response.json()["course_id"] == course.id

    class DummyEngine:
        def generate(self, history, model=None, expect_json=False, max_tokens=1024):
            return {"text": "Course answer"}

    monkeypatch.setattr(chat_service, "get_chat_engine", lambda name, model: DummyEngine())
    monkeypatch.setattr(
        chat_service,
        "semantic_search",
        lambda query, k=3, course_id=None: [{"course_id": course_id, "type": "course_document", "text": "Doc"}],
    )

    patch_response = client.patch(
        f"/api/chat/{chat_id}/model",
        json={"model": "demo-model", "engine": "lc_giga"},
        headers=auth_headers(token),
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["model"] == "demo-model"

    send_response = client.post(
        "/api/chat/send",
        json={"chat_id": chat_id, "text": "Tell me about the course"},
        headers=auth_headers(token),
    )
    assert send_response.status_code == 200
    body = send_response.json()
    assert len(body) == 2
    assert body[-1]["author"] == "bot"
    assert body[-1]["text"] == "Course answer"

    list_response = client.get("/api/chat/", headers=auth_headers(token))
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == chat_id

    history_response = client.get(f"/api/chat/{chat_id}/messages", headers=auth_headers(token))
    assert history_response.status_code == 200
    assert len(history_response.json()) == 2

    delete_response = client.delete(f"/api/chat/{chat_id}", headers=auth_headers(token))
    assert delete_response.status_code == 204

    missing_response = client.get(f"/api/chat/{chat_id}/messages", headers=auth_headers(token))
    assert missing_response.status_code == 404


def test_chat_api_blocks_foreign_chat_access(client, db_session, monkeypatch):
    owner, owner_token = register_and_login(
        client,
        db_session,
        email="chat-owner@example.com",
        password="secret123",
        full_name="Chat Owner",
    )
    _, outsider_token = register_and_login(
        client,
        db_session,
        email="chat-outsider@example.com",
        password="secret123",
        full_name="Chat Outsider",
    )
    course = make_course(db_session, owner_id=owner.id)
    db_session.commit()

    create_response = client.post(
        "/api/chat/",
        json={"name": "Owner chat", "course_id": course.id},
        headers=auth_headers(owner_token),
    )
    chat_id = create_response.json()["id"]

    class DummyEngine:
        def generate(self, history, model=None, expect_json=False, max_tokens=1024):
            return {"text": "No access"}

    monkeypatch.setattr(chat_service, "get_chat_engine", lambda name, model: DummyEngine())

    get_response = client.get(f"/api/chat/{chat_id}/messages", headers=auth_headers(outsider_token))
    assert get_response.status_code == 404

    send_response = client.post(
        "/api/chat/send",
        json={"chat_id": chat_id, "text": "hack"},
        headers=auth_headers(outsider_token),
    )
    assert send_response.status_code == 404

    delete_response = client.delete(f"/api/chat/{chat_id}", headers=auth_headers(outsider_token))
    assert delete_response.status_code == 404
