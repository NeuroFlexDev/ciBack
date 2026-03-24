from app.repositories.chat import ChatRepository
from app.services import chat_service
from tests.factories import make_course, make_user


def test_chat_generate_stores_messages_and_uses_course_context(db_session, monkeypatch):
    user = make_user(db_session, email="chat-service@example.com")
    course = make_course(db_session, owner_id=user.id)
    chat = ChatRepository.create_chat(db_session, user.id, "Service chat", course_id=course.id)

    captured_history = {}

    class DummyEngine:
        def generate(self, history, model=None, expect_json=False, max_tokens=1024):
            captured_history["history"] = history
            return {"text": "Generated answer"}

    monkeypatch.setattr(chat_service, "get_chat_engine", lambda name, model: DummyEngine())
    monkeypatch.setattr(
        chat_service,
        "semantic_search",
        lambda query, k=3, course_id=None: [
            {
                "course_id": course_id,
                "type": "course_document",
                "text": "Important course fact",
                "source_name": "course.txt",
            }
        ],
    )

    result = chat_service.chat_generate(
        chat_id=chat.id,
        user_id=user.id,
        text="What is the key fact?",
        db=db_session,
    )

    assert result["answer"] == "Generated answer"
    messages = ChatRepository.get_history(db_session, chat.id, user.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"
    assert captured_history["history"][0]["role"] == "system"
    assert "Important course fact" in captured_history["history"][0]["content"]
