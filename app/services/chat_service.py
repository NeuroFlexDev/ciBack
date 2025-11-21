# app/services/chat_service.py
from __future__ import annotations
from typing import Any, List
import logging

from app.chat_engine import get_chat_engine
from app.routes.chat_storage import get_history, store_bot_msg, store_user_msg
from app.services.llm_registry import list_models
from app.schemas.chat import MessageOut

# Вывел допом код из app/routes/chat.py сюда

logger = logging.getLogger(__name__)

_fallback_id = iter(range(10_000_000, 10_100_000))  # генератор уникальных ID

def list_available_models() -> List[str]:
    """Возвращает список доступных моделей для чата."""
    models = list_models()
    logger.debug(f"Available models count={len(models)}")
    return models

# Функция преобразует сырые сообщения из хранилища в список Pydantic-схем

def convert_messages(raw: List[dict[str, Any]]) -> List[MessageOut]:
    out: List[MessageOut] = []
    for m in raw:
        mid = m.get("id")
        if mid is None:
            mid = next(_fallback_id)  # гарантированно уникальный int
        role = m.get("role", "user")
        author = "bot" if role == "assistant" else "user"
        out.append(MessageOut(id=int(mid), author=author, text=m.get("content", "")))
    return out


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
