# app/services/chat_service.py
from __future__ import annotations

from typing import Any

from app.chat_engine import get_chat_engine
from app.routes.chat_storage import get_history, store_bot_msg, store_user_msg


def chat_generate(
    *,
    chat_id: int,
    user_id: int,
    text: str,
    engine_name: str = "lc_giga",
    model: str | None = None,
    expect_json: bool = False,
    db: Any = None,  # оставил на будущее, если нужно прокидывать БД
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """
    Генерирует ответ модели и сохраняет оба сообщения (user + bot) в сторедж.
    НИЧЕГО вручную не пушим в history — только через store_*.
    Возвращает полезную инфу (ids, сырой ответ).
    """

    # 1. История до нового сообщения
    history = get_history(chat_id, user_id)

    # 2. Сохраняем сообщение пользователя (получаем его id)
    user_msg_id = store_user_msg(chat_id, text)

    # 3. Формируем историю для модели (локально добавляем текущее юзерское сообщение)
    history_for_llm = history + [{"id": user_msg_id, "role": "user", "content": text}]

    # 4. Берём нужный движок
    engine = get_chat_engine(engine_name, model)

    # 5. Генерация
    res = engine.generate(
        history_for_llm,
        model=model,
        expect_json=expect_json,
    )

    answer = res.get("text") or res.get("choice") or ""

    # 6. Сохраняем ответ бота
    bot_msg_id = store_bot_msg(chat_id, answer)

    # 7. Возвращаем всё, что может пригодиться
    return {
        "answer": answer,
        "raw": res,  # полный ответ движка
        "user_msg_id": user_msg_id,
        "bot_msg_id": bot_msg_id,
    }
