from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.ai import gateway
from app.services import generation_service
from app.services.generation_service import PromptRequest
from scripts.evaluate_materials import _MaterialEvaluation


@pytest.mark.parametrize("maximum, allowed", [(0.01, False), (100, True)])
def test_evaluation_budget_is_checked_before_dispatch(tmp_path, monkeypatch, maximum, allowed):
    evaluator = _MaterialEvaluation()
    evaluator.args = SimpleNamespace(max_rubles=maximum)
    evaluator.out = tmp_path
    evaluator.budget_path = tmp_path / "text-budget.json"
    evaluator.report = {
        "text_budget_reserved_rub": "0",
        "stages": [],
        "repairs": [],
        "prompt_sizes": [],
    }
    dispatch = Mock(return_value={"value": 1})
    monkeypatch.setattr(generation_service, "generate_from_prompt", dispatch)
    monkeypatch.setattr(generation_service, "render_prompt", lambda *_a, **_kw: "rendered")
    monkeypatch.setattr(gateway, "token_count", lambda _messages: 1000)
    request = PromptRequest(
        agent_role="ingestion",
        model="openai/gpt-5.4-mini",
        max_tokens=1000,
        repair_feedback="retry diagnostic",
    )
    if allowed:
        assert evaluator.tracked_generate("example.j2", request=request, topic="example") == {
            "value": 1
        }
        dispatch.assert_called_once_with("example.j2", request=request, topic="example")
        assert evaluator.budget_path.exists()
        assert evaluator.report["repairs"] == [
            {"agent": "ingestion", "diagnostic": "retry diagnostic"}
        ]
    else:
        with pytest.raises(HTTPException) as error:
            evaluator.tracked_generate("example.j2", request=request, topic="example")
        assert error.value.status_code == 402
        assert error.value.request_sent is False
        dispatch.assert_not_called()
        assert evaluator.report["text_budget_reserved_rub"] == "0"
        assert not evaluator.budget_path.exists()
