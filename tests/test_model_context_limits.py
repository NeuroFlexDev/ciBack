"""Removing the installation cap must keep full prompts and real model limits."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.ai import context_budget as budget, gateway, memory, routing
from app.ai.pricing import candidate_price
from app.core.config import AIRoutingCandidate, Settings, settings


@pytest.fixture(autouse=True)
def uncapped(monkeypatch):
    monkeypatch.setattr(settings, "AI_MAX_INPUT_TOKENS", 0)
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [])


@pytest.mark.parametrize("model", list(budget.MODEL_LIMITS))
def test_full_model_window_reserves_output_and_rejects_overflow(monkeypatch, model):
    context, max_output = budget.MODEL_LIMITS[model]
    assert Settings.model_fields["AI_MAX_INPUT_TOKENS"].default == 0
    assert budget.limits(model) == (context, max_output, context)
    monkeypatch.setattr(budget, "count_messages", lambda *args: context - 6000)
    assert budget.require_fits([], model, 6000) == context - 6000
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.require_fits([], model, 6001)
    monkeypatch.setattr(budget, "count_messages", lambda *args: 1)
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.require_fits([], model, max_output + 1)


def test_large_prompt_reaches_gateway_intact_without_the_old_64k_cap(monkeypatch):
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr("test-only"))
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "adaptive")
    monkeypatch.setattr(budget, "_openai_encoding", lambda: None)
    text = "Условие и исключение сохраняются. " * 1600
    messages = [{"role": "user", "content": text}]
    estimate = budget.count_messages(messages, settings.AI_MODEL_FAST)
    assert 64000 < estimate < 200000
    sent = []

    class Model:
        def __init__(self, **kwargs):
            assert kwargs["model"] == settings.AI_MODEL_FAST

        def invoke(self, prompt):
            sent.extend(prompt)
            return SimpleNamespace(content="ok", response_metadata={}, additional_kwargs={},
                                   usage_metadata={"input_tokens": 80000, "output_tokens": 1})

    monkeypatch.setattr(gateway, "ChatOpenAI", Model)
    result = gateway.invoke_messages("ingestion", messages)
    assert [message.content for message in sent] == [text]
    assert result["routing"]["reason"] == "lowest_estimated_cost_per_contract_pass"
    assert result["routing"]["prices"]["max_input_tokens"] > 64000


@pytest.mark.parametrize("overrides,selected", [
    ({}, "test/large"),
    ({"max_input_tokens": 79999}, "openai/gpt-5.4-mini"),
    ({"context_window": 85999}, "openai/gpt-5.4-mini"),
    ({"max_output_tokens": 5999}, "openai/gpt-5.4-mini"),
])
def test_large_prompt_routing_still_respects_each_provider_window(monkeypatch, overrides, selected):
    candidate = AIRoutingCandidate(**{
        "provider": "vsellm", "model": "test/large", "approved": True,
        "roles": ["ingestion"], "input_rub_per_million": 1, "output_rub_per_million": 1,
        "max_input_tokens": 100000, "max_output_tokens": 12000, "context_window": 120000,
        **overrides,
    })
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "adaptive")
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [candidate])
    decision = routing.choose_route(role="ingestion", baseline_model="openai/gpt-5.4-mini",
                                    input_tokens=80000, output_tokens=6000)
    assert decision.selected_model == selected
    if selected == candidate.model:
        assert budget.limits(candidate.model) == (100000, 12000, 120000)


def test_operator_can_still_set_a_smaller_installation_cap(monkeypatch):
    model = "openai/gpt-5.4-mini"
    uncapped_policy = routing.policy_fingerprint()
    monkeypatch.setattr(settings, "AI_MAX_INPUT_TOKENS", 64000)
    monkeypatch.setattr(budget, "count_messages", lambda *args: 64001)
    # Capacity changes must not invalidate completed artifacts or response caches.
    assert routing.policy_fingerprint() == uncapped_policy
    assert budget.limits(model)[0] == 64000
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.require_fits([], model, 6000)
    # A token price band describes the model, not the optional installation cap.
    assert candidate_price("vsellm", model).max_input_tokens == budget.MODEL_LIMITS[model][0]


def test_unknown_model_needs_declared_limits_when_installation_cap_is_disabled():
    with pytest.raises(HTTPException) as error:
        budget.limits("unregistered-model")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "ai_model_limits_missing"


def test_chat_memory_still_summarizes_with_disabled_installation_cap(db_session, auth_user, monkeypatch):
    from app.repositories.chat import ChatRepository
    from app.models.ai_state import ChatMemory
    from app.services import scoped_ai

    monkeypatch.setattr(settings, "CHAT_HISTORY_MESSAGES", 1)
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Memory")
    older = ChatRepository.add_message(db_session, chat.id, "user", "Сохранить моё условие")
    ChatRepository.add_message(db_session, chat.id, "assistant", "Недавний ответ")
    calls = []

    def summarize(*args, **kwargs):
        calls.append(args[4])
        return {"text": "Сохранить моё условие"}

    monkeypatch.setattr(scoped_ai, "tracked_completion", summarize)
    context = memory.conversation_context(db_session, chat.id, auth_user.id, "Продолжим")
    assert len(calls) == 1
    assert db_session.get(ChatMemory, chat.id).through_message_id == older.id
    assert "Сохранить моё условие" in context[0]["content"]
    assert context[-1] == {"role": "user", "content": "Продолжим"}
