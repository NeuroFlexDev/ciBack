from app.routes import chat_storage as cs
from app.services import chat_service


def test_chat_generate(monkeypatch):
    memory = {"messages": [], "chats": {}}

    def fake_get_history(chat_id, user_id):
        return []

    def fake_store_user(chat_id, text):
        memory["messages"].append(("user", text))
        return 1

    def fake_store_bot(chat_id, text):
        memory["messages"].append(("bot", text))
        return 2

    # 1) Разрешаем доступ
    monkeypatch.setitem(cs.CHATS, 1, {"user_id": 2})  # <-- добавьте чат

    # 2) Патчим то, что импортировано ВНУТРИ chat_service
    monkeypatch.setattr(chat_service, "get_history", fake_get_history)
    monkeypatch.setattr(chat_service, "store_user_msg", fake_store_user)
    monkeypatch.setattr(chat_service, "store_bot_msg", fake_store_bot)

    class Dummy:
        def generate(self, hist, model=None, expect_json=False):
            return {"text": "hi"}

    monkeypatch.setattr(chat_service, "get_chat_engine", lambda a, b: Dummy())

    res = chat_service.chat_generate(chat_id=1, user_id=2, text="hello")
    assert res["answer"] == "hi"
