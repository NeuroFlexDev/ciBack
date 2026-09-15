"""OpenAI-compatible VSELLM transport through LangChain; no credentials in logs."""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import HTTPException
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from openai import LengthFinishReasonError
from app.ai.models import model_policy
from app.core.config import settings


def token_count(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    # Tokenizers differ across providers. UTF-8 bytes are a conservative upper
    # bound and require no network/download during a running job. Actual usage
    # replaces the reservation after the response.
    return len(text.encode("utf-8"))


def configured() -> bool:
    return bool(settings.VSELLM_API_KEY.get_secret_value())


def safe_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    return f"provider_http_{status}" if status else type(exc).__name__


def retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 429, 500, 502, 503, 504} or type(exc).__name__ in {
        "APITimeoutError", "OpenAITimeoutError", "APIConnectionError", "OpenAIConnectionError", "TimeoutError", "ConnectionError"
    }


def invoke_messages(role: str, messages: list[dict], *, model: str | None = None,
                    max_tokens: int | None = None, json_mode: bool = False,
                    fallback: bool = False) -> dict:
    if not configured():
        raise HTTPException(503, "VSELLM_API_KEY is not configured")
    policy = model_policy(role)
    used_model = policy.fallback if fallback else (model or policy.model)
    allowed_models = {settings.AI_MODEL_FAST, settings.AI_MODEL_REASONING, settings.AI_MODEL_WRITER,
                      settings.AI_MODEL_CRITIC, settings.AI_MODEL_FALLBACK}
    if used_model not in allowed_models:
        raise HTTPException(422, "This model is not enabled by the server model policy")
    if token_count(messages) > settings.AI_MAX_INPUT_TOKENS:
        raise HTTPException(413, "AI context budget exceeded; split the document or course")
    llm = ChatOpenAI(
        model=used_model,
        api_key=settings.VSELLM_API_KEY.get_secret_value(),
        base_url=settings.VSELLM_BASE_URL,
        timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
        max_tokens=max_tokens or policy.max_output_tokens,
        temperature=None,
    )
    if json_mode:
        llm = llm.bind(response_format={"type": "json_object"})
    constructors = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    try:
        response = llm.invoke([constructors[item["role"]](content=item["content"]) for item in messages])
    except LengthFinishReasonError as exc:
        # The SDK may reject truncated JSON before LangChain returns a message.
        # Preserve actual billed usage while still rejecting the partial content.
        error = HTTPException(502, "AI output was truncated; response rejected")
        completion = exc.completion
        usage = completion.usage
        if usage is not None:
            error.ai_usage = {"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens,
                              "cached_tokens": getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0}
        error.ai_model = completion.model or used_model
        raise error from None
    finish = response.response_metadata.get("finish_reason")
    if finish in {"length", "max_tokens"}:
        error = HTTPException(502, "AI output was truncated; response rejected")
        if response.usage_metadata:
            usage = response.usage_metadata
            error.ai_usage = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                              "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read", 0)}
        error.ai_model = used_model
        raise error
    if finish in {"content_filter", "refusal"} or response.additional_kwargs.get("refusal"):
        raise HTTPException(422, "AI provider declined this request")
    content = response.content
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(502, "AI returned an empty response")
    usage = response.usage_metadata or {}
    return {
        "text": content,
        "model": response.response_metadata.get("model_name") or used_model,
        "usage": {
            "input_tokens": usage.get("input_tokens", token_count(messages)),
            "output_tokens": usage.get("output_tokens", token_count(content)),
            "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read", 0),
        },
    }


def chat_completion(role: str, messages: list[dict], **kwargs) -> dict:
    for attempt in range(settings.AI_MAX_ATTEMPTS):
        try:
            return invoke_messages(role, messages, fallback=attempt > 0, **kwargs)
        except Exception as exc:
            if isinstance(exc, HTTPException) and exc.status_code in {402, 413, 422}:
                raise
            if not retryable(exc) or attempt + 1 == settings.AI_MAX_ATTEMPTS:
                raise HTTPException(503, f"AI unavailable ({safe_error(exc)})") from None
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("unreachable")
