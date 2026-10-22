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


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("markdown", [
    'Порядок действий\n```mermaid\nflowchart LR\n A["Проверка"] --> B["Результат"]\n```\nОбъяснение.',
    '```json\n{"example": true}\n```\n```python\nprint("Пример")\n```',
    '$$E=mc^2$$\n![Иллюстрация](/api/images/7/content)\n```plantuml\n@startuml\nAlice -> Bob: Сообщение\n@enduml\n```',
])
def test_json_response_preserves_nested_markdown_fences(monkeypatch, wrapped, markdown):
    expected = {"lesson_id": "lesson:one", "sections": [{"content_markdown": markdown}],
                "source_refs": ["src:doc:1:v1:chunk:1"]}
    raw = json.dumps(expected, ensure_ascii=False, indent=2)
    if wrapped:
        raw = "```json\n" + raw + "\n```"
    _patch_model_factory(monkeypatch, "gigachat", raw)
    result = generate_from_prompt(prompt="test", engine="gigachat", expect_json=True)
    result.pop("_model")
    assert result == expected
    assert result["sections"][0]["content_markdown"] == markdown


@pytest.mark.parametrize("response", [
    'Unrequested prose\n```json\n{"partial": true}\n```',
    '{"broken": "```json\\n{\\"partial\\": true}\\n```",',
    '```json\n{"partial": true}\n```\n{"extra": true}',
])
def test_embedded_json_fragment_cannot_replace_invalid_outer_response(response):
    from app.services.generation_service import _json_object
    from app.ai.gateway import safe_error
    with pytest.raises(HTTPException) as error:
        _json_object(response)
    assert error.value.ai_failure_kind == "invalid_json"
    assert safe_error(error.value) == "ai_invalid_json"


def test_completed_stages_are_reused_after_parser_upgrade(db_session, auth_user, monkeypatch):
    from pydantic import BaseModel
    from app.models.agent_artifact import AgentArtifact
    from app.services.agent_runtime import AgentRuntime
    from tests.test_ai_runtime_v2 import make_runtime
    class Content(BaseModel):
        content_markdown: str
    markdown = '```mermaid\nflowchart LR\n A --> B\n```'
    _patch_model_factory(monkeypatch, "gigachat", json.dumps({"content_markdown": markdown}))
    calls = []
    def generated(*args, **kwargs):
        calls.append(kwargs["agent_role"])
        return generate_from_prompt(prompt="test", engine="gigachat", expect_json=True)
    runtime = make_runtime(db_session, auth_user, generated)
    def stage(active, role):
        return active.execute(agent=role, artifact="checkpoint", sequence=0,
            template_name="unused.j2", response_model=Content, prompt_context={}, max_tokens=6000)
    for role in ("ingestion", "competency_mapper", "course_architect"):
        assert stage(runtime, role).content_markdown == markdown
    original_ids = [row.id for row in db_session.query(AgentArtifact).order_by(AgentArtifact.id)]
    resumed = AgentRuntime(db=db_session, run_id=runtime.run_id, course_id=runtime.course_id,
        generate=lambda *a, **k: pytest.fail("completed stages must not call a provider on resume"))
    for role in ("ingestion", "competency_mapper", "course_architect"):
        assert stage(resumed, role).content_markdown == markdown
    assert calls == ["ingestion", "competency_mapper", "course_architect"]
    assert [row.id for row in db_session.query(AgentArtifact).order_by(AgentArtifact.id)] == original_ids
