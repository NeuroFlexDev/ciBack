"""Offline prompt accounting and provider/model limits, separate from spend limits.

OpenAI's o200k tokenizer is baked into the image at build time. Unsupported
tokenizers retain the UTF-8 upper bound; counting never downloads at job runtime.
Model limits verified 2026-10-22 against provider and model documentation:
https://vsellm.ru/models/openai/gpt-5.4-mini
https://vsellm.ru/models/anthropic/claude-sonnet-4.6
https://vsellm.ru/models/google/gemini-2.5-flash
https://developers.openai.com/api/docs/models/gpt-5.4-mini
https://platform.claude.com/docs/en/models/sonnet-4-6/overview
https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash
"""
from __future__ import annotations
from app.ai import control as ai_control

import hashlib
import json
import math
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from app.core.config import settings

MODEL_LIMITS = {
    "openai/gpt-5.4-mini": (400000, 128000),
    "anthropic/claude-sonnet-4.6": (1000000, 128000),
    "google/gemini-2.5-flash": (1048576, 65536),
}
TOKENIZER_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"


@lru_cache(maxsize=1)
def _openai_encoding():
    import tiktoken
    from tiktoken.registry import ENCODINGS
    if "o200k_base" in ENCODINGS:
        return ENCODINGS["o200k_base"]
    folder = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get("DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    path = Path(folder) / hashlib.sha1(TOKENIZER_URL.encode()).hexdigest()
    if not folder or not path.is_file():
        return None
    # Validate the bundled bytes ourselves so a corrupt cache cannot cause the
    # library to fetch a replacement over the network while processing a job.
    if hashlib.sha256(path.read_bytes()).hexdigest() != "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d":
        return None
    return tiktoken.get_encoding("o200k_base")


def count_messages(messages: list[dict], model: str) -> int:
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), default=str)
    encoding = _openai_encoding() if model == "openai/gpt-5.4-mini" else None
    if encoding is None:
        return len(serialized.encode("utf-8")) + 32 + 8 * len(messages)
    # Chat framing and provider wrappers are not content tokens. Reserve those
    # separately plus a small safety band, never use a characters/4 heuristic.
    return math.ceil(len(encoding.encode(serialized, disallowed_special=())) * 1.05) + 128 + 8 * len(messages)


class ContextBudgetExceeded(HTTPException):
    def __init__(self, *, model: str, input_tokens: int, output_tokens: int,
                 input_limit: int, context_window: int, request_sent: bool = False):
        super().__init__(413, {"code": "ai_context_exceeded",
            "message": "Запрос превышает доступный контекст. Сохранённые этапы и источники не изменены.",
            "model": model, "input_tokens_estimate": input_tokens,
            "output_tokens_reserved": output_tokens, "input_tokens_limit": input_limit,
            "context_window": context_window})
        self.request_sent = request_sent
        self.ai_failure_kind = "context_length"
        self.ai_model = model


def effective_input_limit(model_limit: int) -> int:
    """Apply an optional installation cap without treating zero as no capacity."""
    cap = settings.AI_MAX_INPUT_TOKENS
    return min(cap, model_limit) if cap else model_limit


def limits(model: str, provider: str = "vsellm") -> tuple[int, int, int]:
    candidate = next((item for item in ai_control.candidates()
                      if item.model == model and item.provider == provider), None)
    if candidate:
        return effective_input_limit(candidate.max_input_tokens), candidate.max_output_tokens, candidate.context_window
    if model in MODEL_LIMITS:
        context, output = MODEL_LIMITS[model]
        return effective_input_limit(context), output, context
    # Unknown static models still need declared limits. An explicit installation
    # cap keeps the legacy conservative behavior; zero must not imply infinity.
    if settings.AI_MAX_INPUT_TOKENS:
        cap = settings.AI_MAX_INPUT_TOKENS
        return cap, cap, cap
    raise HTTPException(503, {"code": "ai_model_limits_missing",
        "message": "Для выбранной AI-модели не настроен размер контекста."})


def require_fits(messages: list[dict], model: str, output_tokens: int,
                 provider: str = "vsellm") -> int:
    estimate = count_messages(messages, model)
    max_input, max_output, context = limits(model, provider)
    if output_tokens > max_output or estimate > max_input or estimate + output_tokens > context:
        raise ContextBudgetExceeded(model=model, input_tokens=estimate,
            output_tokens=output_tokens, input_limit=max_input, context_window=context)
    return estimate


def provider_context_error(exc: Exception) -> bool:
    """Inspect structured error codes only; never log provider prompt echoes."""
    if getattr(exc, "status_code", None) == 413:
        return True
    if getattr(exc, "status_code", None) not in {400, 422}:
        return False
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("code", error.get("type")) in {
            "context_length_exceeded", "max_context_length_exceeded", "prompt_too_long",
            "input_too_long", "context_window_exceeded"}:
            return True
    return getattr(exc, "code", None) in {"context_length_exceeded", "max_context_length_exceeded"}


def prompt_fits(role: str, template: str, context: dict, *, repair_reserve: int = 1500) -> bool:
    from app.ai.models import model_policy
    from app.services.generation_service import render_prompt, agent_messages
    policy = model_policy(role)
    messages = agent_messages(render_prompt(template, **{**context, "external_context": ""}), expect_json=True)
    try:
        # Reserve space for bounded validation feedback before assigning a batch.
        require_fits(messages + [{"role": "system", "content": "x " * repair_reserve}],
                     policy.model, policy.max_output_tokens)
        return True
    except ContextBudgetExceeded:
        return False
