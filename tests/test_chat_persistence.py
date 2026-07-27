from app.models.chat import Chat, ChatMessage
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.core.security import hash_password
from tests.factories import make_course


def test_chat_crud_is_persistent_and_owner_isolated(
    client, db_session, auth_user, auth_headers
):
    created = client.post(
        "/api/chat/", json={"name": "Database chat"}, headers=auth_headers
    )
    chat_id = created.json()["id"]
    db_session.expire_all()

    assert db_session.query(Chat).filter_by(id=chat_id).one().title == "Database chat"
    assert client.get("/api/chat/", headers=auth_headers).json()[0]["id"] == chat_id

    foreign = User(email="foreign-chat@example.com", password_hash=hash_password("secret"))
    db_session.add(foreign)
    db_session.commit()
    assert ChatRepository.get_history(db_session, chat_id, auth_user.id) == []
    try:
        ChatRepository.get_history(db_session, chat_id, foreign.id)
        assert False, "foreign owner must not read chat"
    except KeyError:
        pass


def test_course_binding_requires_owned_course(client, db_session, auth_headers, auth_user):
    owned = make_course(db_session, owner_id=auth_user.id)
    foreign = make_course(db_session, owner_id=None)

    assert client.post(
        "/api/chat/",
        json={"name": "Owned", "course_id": owned.id},
        headers=auth_headers,
    ).status_code == 200
    assert client.post(
        "/api/chat/",
        json={"name": "Foreign", "course_id": foreign.id},
        headers=auth_headers,
    ).status_code == 404


def test_archive_hides_chat_and_preserves_messages(
    client, db_session, auth_user, auth_headers
):
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Archive")
    ChatRepository.add_message(db_session, chat.id, "user", "retained")

    assert client.delete(f"/api/chat/{chat.id}", headers=auth_headers).status_code == 204
    db_session.expire_all()

    archived = db_session.query(Chat).filter_by(id=chat.id).one()
    assert archived.status == "archived"
    assert archived.is_deleted is True
    assert db_session.query(ChatMessage).filter_by(chat_id=chat.id).count() == 1
    assert client.get(f"/api/chat/{chat.id}/messages", headers=auth_headers).status_code == 404


def test_message_history_legacy_shape_and_pagination(
    client, db_session, auth_user, auth_headers
):
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Pages")
    for index in range(4):
        ChatRepository.add_message(db_session, chat.id, "user", f"message-{index}")

    full = client.get(f"/api/chat/{chat.id}/messages", headers=auth_headers)
    page = client.get(
        f"/api/chat/{chat.id}/messages",
        params={"offset": 1, "limit": 2},
        headers=auth_headers,
    )

    assert [item["text"] for item in full.json()] == [
        "message-0", "message-1", "message-2", "message-3"
    ]
    assert [item["text"] for item in page.json()] == ["message-1", "message-2"]
    assert set(page.json()[0]) == {"id", "author", "text", "is_deleted"}


def test_model_patch_persists(client, db_session, auth_user, auth_headers):
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Model")
    response = client.patch(
        f"/api/chat/{chat.id}/model",
        json={"model": "model-a", "engine": "engine-a"},
        headers=auth_headers,
    )
    db_session.expire_all()

    assert response.json() == {"id": chat.id, "model": "model-a", "engine": "engine-a"}
    stored = db_session.query(Chat).filter_by(id=chat.id).one()
    assert (stored.model, stored.engine) == ("model-a", "engine-a")


def test_send_without_chat_id_creates_persistent_chat(
    client, db_session, auth_headers, monkeypatch
):
    def fake_generate(*, chat_id, user_id, text, db, **kwargs):
        user_message = ChatRepository.add_message(db, chat_id, "user", text)
        bot_message = ChatRepository.add_message(db, chat_id, "assistant", "answer")
        return {"user_msg_id": user_message.id, "bot_msg_id": bot_message.id}

    monkeypatch.setattr("app.routes.chat.chat_generate", fake_generate)
    response = client.post(
        "/api/chat/send", json={"text": "hello"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert [item["author"] for item in response.json()] == ["user", "bot"]
    assert db_session.query(Chat).filter_by(title="Новый чат").count() == 1


def test_runtime_does_not_import_legacy_chat_storage():
    runtime_files = [
        "app/repositories/chat.py",
        "app/services/chat_service.py",
        "app/routes/chat.py",
    ]
    for path in runtime_files:
        with open(path, encoding="utf-8") as source:
            assert "chat_storage" not in source.read()
