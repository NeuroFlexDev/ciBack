# tests/test_generation_service.py
import json

import pytest

from app.services.generation_service import generate_from_prompt


class DummyClient:
    def __init__(self, text):
        self._text = text

    def generate(self, prompt, max_tokens=None):
        return self._text


def _patch_engine(monkeypatch, engine_name, reply_text):
    def ctor(_model):
        return DummyClient(reply_text), "dummy-model"

    monkeypatch.setitem(
        # импортируем прямо из файла с SUPPORTED_ENGINES
        __import__(
            "app.services.generation_service", fromlist=["SUPPORTED_ENGINES"]
        ).SUPPORTED_ENGINES,
        engine_name,
        ctor,
    )


def test_json_ok(monkeypatch):
    json_reply = json.dumps({"modules": [{"title": "X"}]})
    _patch_engine(monkeypatch, "gigachat", json_reply)

    res = generate_from_prompt(
        template_name=None,
        prompt="test",
        engine="gigachat",
        expect_json=True,
    )
    assert res["modules"][0]["title"] == "X"
    assert res["_model"] == "dummy-model"


def test_json_fail(monkeypatch):
    _patch_engine(monkeypatch, "gigachat", '{"bad":1}\n{extra}')
    with pytest.raises(Exception):
        generate_from_prompt(prompt="x", engine="gigachat", expect_json=True)


def test_alias(monkeypatch):
    _patch_engine(monkeypatch, "gigachat", json.dumps({"ok": 1}))
    res = generate_from_prompt(prompt="x", engine="lc_giga", expect_json=True)
    assert res["ok"] == 1
