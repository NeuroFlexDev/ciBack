"""Always-available local responses. Never impersonates a completed AI task."""
from __future__ import annotations

import re


GENERATION_PAUSED = (
    "Материалы и готовые этапы сохранены. Сейчас генерацию не удалось продолжить. "
    "Вы можете работать с доступными материалами и продолжить генерацию позже."
)
GENERATION_QUEUED = (
    "Материалы и готовые этапы сохранены. Генерация ожидает восстановления связи; "
    "мы автоматически попробуем продолжить её."
)
CANVAS_UNAVAILABLE = (
    "Сейчас доступно ручное редактирование канвы. AI не смог подготовить проверенное "
    "предложение; ваши узлы не изменены. Повторите действие позже."
)


def source_answer(query: str, citations=()) -> dict:
    terms = {word[:5] for word in re.findall(r"[\w]{4,}", query.casefold())
             if word not in {"какой", "какая", "какие", "нужно", "делать", "можно", "такое", "what", "does", "when"}}
    excerpts = []
    for citation in citations:
        text = citation.text if hasattr(citation, "text") else citation["text"]
        chunk_id = citation.chunk_id if hasattr(citation, "chunk_id") else citation["chunk_id"]
        # A citation is an exact excerpt, not an inferred answer. Empty lexical
        # matches must not be presented as relevant evidence.
        if terms and any(term in text.casefold() for term in terms):
            quote = text[:1200]
            excerpts.append("\n".join("> " + line for line in quote.splitlines()) + f"\n[chunk:{int(chunk_id)}]")
        if len(excerpts) == 3:
            break
    if excerpts:
        text = ("Сейчас я работаю в локальном режиме. Нашёл в материалах курса такие выдержки. "
                "Это точные цитаты, а не сгенерированное объяснение:\n\n" + "\n\n".join(excerpts)
                + "\n\nДля развёрнутого объяснения повторите вопрос позже.")
    else:
        text = ("Сейчас доступен локальный режим. Я не нашёл в доступных материалах выдержек, "
                "которые можно уверенно связать с вашим вопросом. Сообщение останется в истории; "
                "можно уточнить вопрос, продолжить работу с курсом или повторить запрос позже.")
    return {"text": text, "model": "local/extractive", "provider": "local",
            "degraded": True, "usage_reported": False,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}}
