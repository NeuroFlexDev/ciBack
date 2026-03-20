import json

import pytest
import requests
from fastapi import HTTPException

from app.services import generation_service as gs
from app.services.gigachat_service import GigaChatClient
from app.services.hf_infer_service import HFClientWrapper
from app.services.llm_types import LLMError


def test_generate_from_prompt_retries_retryable_error(monkeypatch):
    calls = {"count": 0}

    class FlakyClient:
        def generate(self, prompt, max_tokens=1024):
            calls["count"] += 1
            if calls["count"] == 1:
                raise LLMError(
                    code="timeout",
                    message="temporary timeout",
                    provider="gigachat",
                    model="dummy-model",
                    status_code=504,
                    retryable=True,
                )
            return json.dumps({"ok": 1})

    monkeypatch.setitem(gs.SUPPORTED_ENGINES, "gigachat", lambda _model: (FlakyClient(), "dummy-model"))
    monkeypatch.setattr("app.services.llm_registry.time.sleep", lambda _seconds: None)

    result = gs.generate_from_prompt(prompt="hello", engine="gigachat", expect_json=True)

    assert result["ok"] == 1
    assert result["_attempts"] == 2
    assert result["_provider"] == "gigachat"
    assert calls["count"] == 2


def test_generate_from_prompt_returns_controlled_http_error(monkeypatch):
    class BrokenClient:
        def generate(self, prompt, max_tokens=1024):
            raise LLMError(
                code="provider_error",
                message="provider unavailable",
                provider="gigachat",
                model="broken-model",
                status_code=503,
                retryable=False,
            )

    monkeypatch.setitem(gs.SUPPORTED_ENGINES, "gigachat", lambda _model: (BrokenClient(), "broken-model"))

    with pytest.raises(HTTPException) as exc_info:
        gs.generate_from_prompt(prompt="hello", engine="gigachat", expect_json=True)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "provider unavailable"


def test_hf_client_wrapper_translates_timeout():
    class DummyInferenceClient:
        def text_generation(self, *args, **kwargs):
            raise requests.Timeout("timeout")

    wrapper = HFClientWrapper(DummyInferenceClient(), model="hf-model")

    with pytest.raises(LLMError) as exc_info:
        wrapper.generate("hello")

    assert exc_info.value.code == "timeout"
    assert exc_info.value.retryable is True
    assert exc_info.value.provider == "hf_api"


def test_gigachat_client_collects_usage(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": "hello from giga"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    monkeypatch.setattr("app.services.gigachat_service._get_access_token", lambda _scope: "token")
    monkeypatch.setattr("app.services.gigachat_service.requests.post", lambda *args, **kwargs: DummyResponse())

    client = GigaChatClient("giga-model")
    text = client.generate("hello")

    assert text == "hello from giga"
    assert client.last_invocation.token_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert client.last_invocation.finish_reason == "stop"
