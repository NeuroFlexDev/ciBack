import json
import logging

import pytest
from fastapi import HTTPException

from app.services.generation_service import generate_from_prompt


class DummyClient:
    def __init__(self, response, *, model=None):
        self._response = response
        self.model = model
        self.calls = []

    def generate(self, prompt, max_tokens=None):
        self.calls.append((prompt, max_tokens))
        return self._response


def _patch_model_factory(monkeypatch, engine_name, response, used_model="dummy-model"):
    observed = {}

    def factory(model):
        observed["model"] = model
        observed["client"] = DummyClient(response)
        return observed["client"], used_model

    monkeypatch.setitem(
        __import__(
            "app.services.generation_service", fromlist=["SUPPORTED_ENGINES"]
        ).SUPPORTED_ENGINES,
        engine_name,
        factory,
    )
    return observed


def test_json_ok(monkeypatch):
    observed = _patch_model_factory(
        monkeypatch,
        "gigachat",
        json.dumps({"modules": [{"title": "X"}]}),
    )

    result = generate_from_prompt(
        template_name=None,
        prompt="test",
        engine="gigachat",
        expect_json=True,
    )

    assert result["modules"][0]["title"] == "X"
    assert result["_model"] == "dummy-model"
    assert observed["model"] is None
    assert observed["client"].calls == [("test", 1024)]


def test_json_fail(monkeypatch):
    _patch_model_factory(monkeypatch, "gigachat", '{"bad":1}\n{extra}')

    with pytest.raises(HTTPException) as exc_info:
        generate_from_prompt(prompt="x", engine="gigachat", expect_json=True)

    assert exc_info.value.status_code == 500


def test_lc_giga_alias(monkeypatch):
    _patch_model_factory(monkeypatch, "gigachat", json.dumps({"ok": 1}))

    result = generate_from_prompt(prompt="x", engine="lc_giga", expect_json=True)

    assert result["ok"] == 1


def test_huggingface_alias_supports_no_argument_factory(monkeypatch):
    def factory():
        return DummyClient(json.dumps({"ok": True, "_model": "untrusted"}))

    monkeypatch.setitem(
        __import__(
            "app.services.generation_service", fromlist=["SUPPORTED_ENGINES"]
        ).SUPPORTED_ENGINES,
        "hf_api",
        factory,
    )

    result = generate_from_prompt(prompt="x", engine="huggingface")

    assert result == {"ok": True}
    assert "_model" not in result


def test_extracts_json_fence_without_stripping_json_characters(monkeypatch):
    _patch_model_factory(
        monkeypatch,
        "gigachat",
        '```json\n{"name": "reason", "ok": true}\n```',
    )

    result = generate_from_prompt(prompt="x", engine="gigachat")

    assert result["name"] == "reason"
    assert result["ok"] is True


def test_accepts_dict_response(monkeypatch):
    _patch_model_factory(
        monkeypatch,
        "gigachat",
        {"ok": True, "value": 7},
    )

    result = generate_from_prompt(prompt="x", engine="gigachat")

    assert result["ok"] is True
    assert result["value"] == 7


def test_accepts_text_wrapped_dict_response(monkeypatch):
    _patch_model_factory(
        monkeypatch,
        "gigachat",
        {"text": json.dumps({"ok": True})},
    )

    result = generate_from_prompt(prompt="x", engine="gigachat")

    assert result["ok"] is True


def test_passes_requested_model_to_factory_and_returns_actual_model(monkeypatch):
    observed = _patch_model_factory(
        monkeypatch,
        "gigachat",
        json.dumps({"ok": True}),
        used_model="actual-model",
    )

    result = generate_from_prompt(
        prompt="x",
        engine="gigachat",
        model="requested-model",
    )

    assert observed["model"] == "requested-model"
    assert result["_model"] == "actual-model"


def test_prompt_and_raw_response_are_not_logged(monkeypatch, caplog):
    secret_prompt = "private prompt body"
    secret_response = "private response body"
    _patch_model_factory(
        monkeypatch,
        "gigachat",
        json.dumps({"content": secret_response}),
    )
    caplog.set_level(logging.INFO, logger="app.services.generation_service")

    generate_from_prompt(prompt=secret_prompt, engine="gigachat")

    assert secret_prompt not in caplog.text
    assert secret_response not in caplog.text
