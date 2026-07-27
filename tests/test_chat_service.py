import pytest

from app.models.chat import ChatMessage
from app.repositories.chat import ChatRepository
from app.services import chat_service


class DummyEngine:
    def __init__(self, response=None, error=None):
        self.response = response or {"text": "hi"}
        self.error = error
        self.history = None

    def generate(self, history, **kwargs):
        self.history = history
        if self.error:
            raise self.error
        return self.response


def test_chat_generate_persists_messages_and_usage(db_session, auth_user, monkeypatch):
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Persistent")
    engine = DummyEngine({
        "text": "hi",
        "model": "answer-model",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    })
    monkeypatch.setattr(chat_service, "get_chat_engine", lambda *_: engine)

    result = chat_service.chat_generate(
        chat_id=chat.id, user_id=auth_user.id, text="hello", db=db_session
    )

    messages = ChatRepository.get_history(db_session, chat.id, auth_user.id)
    assert result["answer"] == "hi"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].model == "answer-model"
    assert messages[1].total_tokens == 5


def test_chat_generate_limits_llm_history_but_retains_all(
    db_session, auth_user, monkeypatch
):
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Long")
    for index in range(25):
        ChatRepository.add_message(db_session, chat.id, "user", str(index))
    engine = DummyEngine()
    monkeypatch.setattr(chat_service, "get_chat_engine", lambda *_: engine)
    monkeypatch.setattr(chat_service.settings, "CHAT_HISTORY_MESSAGES", 20)

    chat_service.chat_generate(
        chat_id=chat.id, user_id=auth_user.id, text="latest", db=db_session
    )

    assert len(engine.history) == 21
    assert engine.history[0]["content"] == "5"
    assert len(ChatRepository.get_history(db_session, chat.id, auth_user.id)) == 27


def test_llm_failure_keeps_user_message(db_session, auth_user, monkeypatch):
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Failure")
    monkeypatch.setattr(
        chat_service,
        "get_chat_engine",
        lambda *_: DummyEngine(error=RuntimeError("provider unavailable")),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        chat_service.chat_generate(
            chat_id=chat.id, user_id=auth_user.id, text="keep me", db=db_session
        )

    messages = db_session.query(ChatMessage).filter_by(chat_id=chat.id).all()
    assert [(message.role, message.content) for message in messages] == [
        ("user", "keep me")
    ]
