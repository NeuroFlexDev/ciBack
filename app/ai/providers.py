"""Credential isolation and shared provider circuits for OpenAI-compatible text APIs."""
from __future__ import annotations

import hashlib
import os
import threading
import time

from fastapi import HTTPException
from redis import Redis, RedisError

from app.core.config import AIProvider, settings


class AIUnavailable(HTTPException):
    def __init__(self):
        super().__init__(503, "AI execution is temporarily unavailable; progress is preserved")
        self.request_sent = False


def registry() -> dict[str, AIProvider]:
    providers = {"vsellm": AIProvider(id="vsellm", base_url=settings.VSELLM_BASE_URL,
                 api_key_env="VSELLM_API_KEY", timeout_seconds=settings.AI_REQUEST_TIMEOUT_SECONDS)}
    providers.update({item.id: item for item in settings.AI_PROVIDERS})
    return providers


def credential(provider: AIProvider) -> str:
    if not provider.api_key_env:
        return "local-no-key" if provider.local else ""
    value = getattr(settings, provider.api_key_env, None)
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    return os.getenv(provider.api_key_env, "")


def configured(provider: AIProvider) -> bool:
    return provider.enabled and bool(credential(provider))


_circuits: dict[str, float] = {}
_lock = threading.Lock()


def _key(provider: AIProvider) -> str:
    digest = hashlib.sha256(str(provider.base_url).encode()).hexdigest()[:24]
    return f"lernium:ai:circuit:{provider.id}:{digest}"


def _redis():
    return Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.1, socket_timeout=0.1)


def available(provider: AIProvider) -> bool:
    if not configured(provider):
        return False
    key = _key(provider)
    with _lock:
        if _circuits.get(key, 0) > time.time():
            return False
    if settings.ENV != "test":
        try:
            return not bool(_redis().exists(key))
        except RedisError:
            pass  # A Redis outage must not disable the inference gateway.
    return True


def mark_unavailable(provider: AIProvider) -> None:
    ttl = settings.AI_PROVIDER_COOLDOWN_SECONDS
    key = _key(provider)
    with _lock:
        _circuits[key] = time.time() + ttl
    if settings.ENV != "test":
        try:
            _redis().set(key, "1", ex=ttl)
        except RedisError:
            pass


def route_id(provider: str, model: str) -> str:
    return f"{provider}::{model}"
