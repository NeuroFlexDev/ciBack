from __future__ import annotations

import base64
import time
import uuid

import requests

from app.core.config import settings
from app.services.llm_types import LLMClient, LLMError, LLMInvocationMeta
from log import get_logger

logger = get_logger(__name__)

GIGA_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGA_API_BASE = "https://gigachat.devices.sberbank.ru/api/v1"

_token_cache = {"val": None, "exp": 0}


class GigaChatClient(LLMClient):
    def __init__(self, model: str, scope: str = "GIGACHAT_API_PERS"):
        self.model = model
        self.scope = scope
        self.last_invocation = LLMInvocationMeta(provider="gigachat", model=model)

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        token = _get_access_token(self.scope)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "n": 1,
            "stream": False,
            "repetition_penalty": 1.0,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            response = requests.post(
                f"{GIGA_API_BASE}/chat/completions",
                json=payload,
                headers=headers,
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
                verify=True,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise LLMError(
                code="timeout",
                message="GigaChat request timed out",
                provider="gigachat",
                model=self.model,
                status_code=504,
                retryable=True,
            ) from exc
        except requests.HTTPError as exc:
            raise LLMError(
                code="provider_http_error",
                message="GigaChat request failed",
                provider="gigachat",
                model=self.model,
                status_code=503,
                retryable=False,
                details={"response_status": exc.response.status_code if exc.response else None},
            ) from exc
        except Exception as exc:
            error_text = f"{exc.__class__.__name__}: {exc}".lower()
            if "timeout" in error_text:
                raise LLMError(
                    code="timeout",
                    message="GigaChat request timed out",
                    provider="gigachat",
                    model=self.model,
                    status_code=504,
                    retryable=True,
                ) from exc
            raise LLMError(
                code="provider_error",
                message="GigaChat request failed",
                provider="gigachat",
                model=self.model,
                status_code=503,
                retryable=False,
                details={"error_type": exc.__class__.__name__},
            ) from exc

        payload_json = response.json()
        choice = payload_json["choices"][0]["message"]["content"]
        usage = payload_json.get("usage")
        finish_reason = None
        if payload_json.get("choices"):
            finish_reason = payload_json["choices"][0].get("finish_reason")
        self.last_invocation = LLMInvocationMeta(
            provider="gigachat",
            model=self.model,
            token_usage=usage,
            finish_reason=finish_reason,
        )
        return choice


def _basic_header() -> str:
    if not settings.GIGA_CLIENT_ID or not settings.GIGA_CLIENT_SECRET:
        raise LLMError(
            code="configuration_error",
            message="GigaChat credentials are not configured",
            provider="gigachat",
            status_code=503,
            retryable=False,
        )
    credentials = f"{settings.GIGA_CLIENT_ID}:{settings.GIGA_CLIENT_SECRET}"
    return base64.b64encode(credentials.encode()).decode()


def _get_access_token(scope: str) -> str:
    now = time.time()
    if _token_cache["val"] and now < _token_cache["exp"] - 60:
        return _token_cache["val"]

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {_basic_header()}",
    }
    data = {"scope": scope}
    try:
        response = requests.post(
            GIGA_OAUTH_URL,
            headers=headers,
            data=data,
            timeout=settings.GIGA_OAUTH_TIMEOUT_SECONDS,
            verify=True,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise LLMError(
            code="timeout",
            message="GigaChat token request timed out",
            provider="gigachat",
            status_code=504,
            retryable=True,
        ) from exc
    except requests.HTTPError as exc:
        raise LLMError(
            code="provider_http_error",
            message="GigaChat token request failed",
            provider="gigachat",
            status_code=503,
            retryable=False,
            details={"response_status": exc.response.status_code if exc.response else None},
        ) from exc
    except Exception as exc:
        error_text = f"{exc.__class__.__name__}: {exc}".lower()
        if "timeout" in error_text:
            raise LLMError(
                code="timeout",
                message="GigaChat token request timed out",
                provider="gigachat",
                status_code=504,
                retryable=True,
            ) from exc
        raise LLMError(
            code="provider_error",
            message="GigaChat token request failed",
            provider="gigachat",
            status_code=503,
            retryable=False,
            details={"error_type": exc.__class__.__name__},
        ) from exc

    payload_json = response.json()
    token = payload_json["access_token"]
    expires_at_ms = payload_json.get("expires_at", int((now + 1700) * 1000))
    _token_cache["val"] = token
    _token_cache["exp"] = expires_at_ms / 1000
    return token


def list_gigachat_models(scope: str | None = None) -> list[str]:
    token = _get_access_token(scope or settings.GIGA_SCOPE)
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        response = requests.get(
            f"{GIGA_API_BASE}/models",
            headers=headers,
            timeout=settings.GIGA_MODELS_TIMEOUT_SECONDS,
            verify=True,
        )
        response.raise_for_status()
    except Exception as exc:
        raise LLMError(
            code="provider_error",
            message="Unable to fetch GigaChat models",
            provider="gigachat",
            status_code=503,
            retryable=False,
            details={"error_type": exc.__class__.__name__},
        ) from exc

    payload_json = response.json()
    models = payload_json["data"] if isinstance(payload_json, dict) and "data" in payload_json else payload_json
    ids: list[str] = []
    for model in models:
        model_id = model.get("id") or model.get("model")
        if model_id:
            ids.append(model_id)
    return ids


def get_gigachat_client(preferred_model: str | None = None) -> tuple[LLMClient, str]:
    models = list_gigachat_models()
    if not models:
        raise LLMError(
            code="configuration_error",
            message="No available GigaChat models found",
            provider="gigachat",
            model=preferred_model,
            status_code=503,
            retryable=False,
        )
    used_model = preferred_model if preferred_model in models else models[0]
    return GigaChatClient(used_model, scope=settings.GIGA_SCOPE), used_model


def get_available_giga_models() -> list[str]:
    try:
        return list_gigachat_models()
    except Exception as exc:
        logger.warning("gigachat available models failed error=%s", exc.__class__.__name__)
        return []
