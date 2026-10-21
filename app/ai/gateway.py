"""Routed OpenAI-compatible text transport through LangChain; isolated credentials."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from openai import LengthFinishReasonError
from app.ai.models import model_policy
from app.ai.routing import enabled_candidates, choose_route
from app.ai import providers
from app.core.config import settings


def token_count(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    # Tokenizers differ across providers. UTF-8 bytes are a conservative upper
    # bound and require no network/download during a running job. Actual usage
    # replaces the reservation after the response.
    return len(text.encode("utf-8"))


def configured() -> bool:
    return any(providers.configured(item) for item in providers.registry().values())


def available_provider_ids() -> set[str]:
    return {name for name, item in providers.registry().items() if providers.available(item)}


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
                    fallback: bool = False, provider: str | None = None,
                    excluded_routes: set[str] | None = None) -> dict:
    if not configured():
        raise providers.AIUnavailable()
    policy = model_policy(role)
    used_model = policy.fallback if fallback else (model or policy.model)
    allowed_models = {settings.AI_MODEL_FAST, settings.AI_MODEL_REASONING, settings.AI_MODEL_WRITER,
                      settings.AI_MODEL_CRITIC, settings.AI_MODEL_FALLBACK}
    static_models = set(allowed_models)
    if settings.AI_ROUTING_MODE == "adaptive":
        allowed_models.update(item.model for item in enabled_candidates(role))
    if used_model not in allowed_models:
        raise HTTPException(422, "This model is not enabled by the server model policy")
    input_estimate = token_count(messages)
    output_limit = max_tokens or policy.max_output_tokens
    if input_estimate > settings.AI_MAX_INPUT_TOKENS:
        raise HTTPException(413, "AI context budget exceeded; split the document or course")
    decision = None
    if provider is None:
        # Normal calls are routed before sending, not only after an error/limit.
        from app.ai import token_forecast
        request_features = token_forecast.features(messages, role)
        predictions = {providers.route_id(item.provider, item.model): token_forecast.predict(
            token_forecast.current_model(), role=role, model_id=item.model,
            request_features=request_features, max_output_tokens=output_limit,
        ) for item in enabled_candidates(role)}
        predictions[providers.route_id("vsellm", used_model)] = token_forecast.predict(
            token_forecast.current_model(), role=role, model_id=used_model,
            request_features=request_features, max_output_tokens=output_limit)
        decision = choose_route(role=role, baseline_model=used_model,
                                input_tokens=input_estimate, output_tokens=output_limit,
                                available_providers=available_provider_ids(),
                                excluded_models=excluded_routes,
                                requested_model=model if not fallback else None,
                                forecast=predictions)
        provider, used_model = decision.selected_provider, decision.selected_model
    selected_provider = providers.registry().get(provider)
    if selected_provider is None or not providers.available(selected_provider):
        raise providers.AIUnavailable()
    candidate = next((item for item in enabled_candidates(role)
                      if item.model == used_model and item.provider == provider), None)
    if candidate is None and not (provider == "vsellm" and used_model in static_models):
        # Approval is for a provider/model pair. A model approved at another
        # endpoint must never silently leak into the baseline provider fallback.
        raise providers.AIUnavailable()
    if used_model in static_models and provider == "vsellm":
        candidate = None
    if candidate and (input_estimate > candidate.max_input_tokens
                      or output_limit > candidate.max_output_tokens
                      or input_estimate + output_limit > candidate.context_window):
        raise HTTPException(413, "AI request exceeds the approved model context or price band")
    llm = ChatOpenAI(
        model=used_model,
        api_key=providers.credential(selected_provider),
        base_url=str(selected_provider.base_url),
        timeout=selected_provider.timeout_seconds,
        max_retries=0,
        max_tokens=output_limit,
        temperature=None,
        **({"extra_body": candidate.extra_body} if candidate and candidate.extra_body else {}),
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
        error.ai_failure_kind = "truncated"
        completion = exc.completion
        usage = completion.usage
        if usage is not None:
            error.ai_usage = {"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens,
                              "cached_tokens": getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0}
        error.ai_model = completion.model or used_model
        error.ai_provider = provider
        raise error from None
    except Exception as exc:
        if retryable(exc) or getattr(exc, "status_code", None) in {401, 402, 403}:
            providers.mark_unavailable(selected_provider)
            error = providers.AIUnavailable()
            error.request_sent = True
            error.ai_model = used_model
            error.ai_provider = provider
            raise error from None
        raise
    usage = response.usage_metadata or {}
    reported_usage = {
        "input_tokens": usage.get("input_tokens", input_estimate),
        "output_tokens": usage.get("output_tokens", 0),
        "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read", 0),
    }

    def reject(status: int, detail: str, kind: str) -> HTTPException:
        error = HTTPException(status, detail)
        error.ai_failure_kind = kind
        error.ai_model = response.response_metadata.get("model_name") or used_model
        error.ai_provider = provider
        if response.usage_metadata:
            error.ai_usage = reported_usage
        return error

    finish = response.response_metadata.get("finish_reason")
    if finish in {"length", "max_tokens"}:
        raise reject(502, "AI output was truncated; response rejected", "truncated")
    if finish in {"content_filter", "refusal"} or response.additional_kwargs.get("refusal"):
        raise reject(422, "AI provider declined this request", "refusal")
    content = response.content
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise reject(502, "AI returned an empty response", "empty")
    return {
        "text": content,
        "model": response.response_metadata.get("model_name") or used_model,
        "provider": provider,
        "routing": decision.metadata if decision else None,
        "usage_reported": bool(response.usage_metadata),
        "usage": {
            "input_tokens": usage.get("input_tokens", input_estimate),
            "output_tokens": usage.get("output_tokens", token_count(content)),
            "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read", 0),
        },
    }


def chat_completion(role: str, messages: list[dict], **kwargs) -> dict:
    excluded_routes = set()
    for attempt in range(settings.AI_MAX_ATTEMPTS):
        try:
            return invoke_messages(role, messages, fallback=attempt > 0,
                                   excluded_routes=excluded_routes, **kwargs)
        except Exception as exc:
            if isinstance(exc, HTTPException) and exc.status_code in {402, 413, 422}:
                raise
            if getattr(exc, "ai_model", None):
                excluded_routes.add(providers.route_id(getattr(exc, "ai_provider", "vsellm"), exc.ai_model))
            if not retryable(exc) or attempt + 1 == settings.AI_MAX_ATTEMPTS:
                raise providers.AIUnavailable() from None
    raise RuntimeError("unreachable")
