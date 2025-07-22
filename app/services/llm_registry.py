# app/services/llm_registry.py
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda

from app.services.gigachat_service import get_available_giga_models, get_gigachat_client
from app.services.hf_infer_service import get_available_hf_models, get_hf_client
from app.services.llm_types import (
    LLMClient,  # твой интерфейс client.generate(prompt, max_tokens=...)
)

# ---------- Внутренние утилиты ----------


def _wrap_client_as_runnable(client: LLMClient, max_tokens: int = 1024):
    """
    Делает из нашего простого LLM-клиента Runnable, чтобы его можно было
    ставить в пайплайн LangChain: prompt | llm | StrOutputParser().
    """

    def _run(inp: Any) -> str:
        # prompt может прийти как dict из prompt шаблона ({"question": "..."}), либо просто строкой
        if isinstance(inp, dict):
            # популярные ключи
            prompt = inp.get("question") or inp.get("input") or inp.get("prompt") or ""
        else:
            prompt = str(inp)
        raw = client.generate(prompt, max_tokens=max_tokens)
        # client.generate должен возвращать {"text": "..."} или строку.
        if isinstance(raw, dict):
            return raw.get("text", "")
        return str(raw)

    return RunnableLambda(_run)


# ---------- Публичные функции ----------


def get_llm(model: str | None = None, engine: str | None = None):
    """
    Возвращает Runnable (LLM) для LangChain.
    Если engine не указан — пытаемся по модели определить,
    иначе берём дефолт (HF).
    """
    # Явный движок (hf_api / gigachat)
    if engine == "gigachat":
        client, used = get_gigachat_client(model)
        return _wrap_client_as_runnable(client)
    if engine == "hf_api" or engine is None:
        client, used = get_hf_client(model)
        return _wrap_client_as_runnable(client)

    # если попадёт неизвестный — швыряемся
    raise ValueError(f"Unknown engine '{engine}'")


def list_models() -> list[str]:
    """Отдать список всех доступных моделей (HF + GigaChat)."""
    return get_available_hf_models() + get_available_giga_models()
