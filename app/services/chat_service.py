from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.chat_engine import get_chat_engine
from app.core.config import settings
from app.models.chat import ChatMessage
from app.repositories.chat import ChatRepository
from app.schemas.chat import MessageOut
from app.services.llm_registry import list_models

logger = logging.getLogger(__name__)


def list_available_models() -> list[str]:
    models = list_models()
    logger.debug("Available models count=%d", len(models))
    return models


def convert_messages(messages: list[ChatMessage]) -> list[MessageOut]:
    return [
        MessageOut(
            id=message.id,
            author="bot" if message.role == "assistant" else "user",
            text=message.content,
            is_deleted=message.is_deleted,
        )
        for message in messages
    ]


def _usage_from_response(response: dict[str, Any]) -> dict[str, int | None]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if total is None and isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    return {
        "prompt_tokens": prompt if isinstance(prompt, int) else None,
        "completion_tokens": completion if isinstance(completion, int) else None,
        "total_tokens": total if isinstance(total, int) else None,
    }


def chat_generate(
    *, chat_id: int, user_id: int, text: str, db: Session,
    engine_name: str = "lc_giga", model: str | None = None,
    expect_json: bool = False, max_tokens: int = 1024,
) -> dict[str, Any]:
    history = ChatRepository.get_recent_history(
        db, chat_id, user_id, settings.CHAT_HISTORY_MESSAGES
    )
    user_message = ChatRepository.add_message(db, chat_id, "user", text)
    llm_history = [
        {"role": message.role, "content": message.content} for message in history
    ]
    llm_history.append({"role": "user", "content": text})

    response = get_chat_engine(engine_name, model).generate(
        llm_history, model=model, expect_json=expect_json
    )
    answer = response.get("text") or response.get("choice") or ""
    response_model = response.get("model")
    usage = _usage_from_response(response)
    assistant_message = ChatRepository.add_message(
        db, chat_id, "assistant", answer,
        model=response_model if isinstance(response_model, str) else model,
        metadata={"engine": engine_name}, **usage,
    )
    return {
        "answer": answer,
        "raw": response,
        "user_msg_id": user_message.id,
        "bot_msg_id": assistant_message.id,
    }
