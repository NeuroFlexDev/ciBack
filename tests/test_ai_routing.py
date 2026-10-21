from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.ai import gateway, routing
from app.core.config import AIRoutingCandidate, settings
from app.models.ai_state import AICall
from app.models.generation_run import GenerationRun
from app.services import generation_service
from app.services.agent_runtime import AgentRuntime
from app.models.user import User
from tests.test_ai_runtime_v2 import execute, make_runtime


def candidate(model="test/cheap", **overrides):
    return AIRoutingCandidate(**{
        "model": model, "roles": ["ingestion"], "approved": True, "tier": 0,
        "input_rub_per_million": "3", "output_rub_per_million": "14",
        "max_input_tokens": 32000, "max_output_tokens": 12000, "context_window": 64000,
        **overrides,
    })


@pytest.fixture
def adaptive(monkeypatch):
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr("test-only"))
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "adaptive")
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [candidate()])
    monkeypatch.setattr(gateway, "configured", lambda: True)
    monkeypatch.setattr(generation_service, "render_prompt", lambda *a, **kw: "prompt")


def choose(**kwargs):
    return routing.choose_route(**{
        "role": "ingestion", "baseline_model": settings.AI_MODEL_FAST,
        "input_tokens": 1000, "output_tokens": 6000, "now": 1000, **kwargs,
    })


def observations(outcomes, **kwargs):
    return [{"policy": routing.policy_fingerprint(), "input_bucket": "short",
             "selected_model": "test/cheap", "outcome": outcome,
             "finished_at": 999 - index, **kwargs} for index, outcome in enumerate(outcomes)]


def test_adaptive_selects_low_cost_and_shadow_preserves_existing_model(adaptive, monkeypatch):
    assert choose().selected_model == "test/cheap"
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "shadow")
    decision = choose()
    assert decision.selected_model == settings.AI_MODEL_FAST
    assert decision.proposed_model == "test/cheap"
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "off")
    assert choose().selected_model == settings.AI_MODEL_FAST
    assert routing.cache_policy() == {}


@pytest.mark.parametrize("overrides", [
    {"approved": False}, {"roles": ["lesson_writer"]}, {"json_mode": False},
    {"max_input_tokens": 999}, {"max_output_tokens": 5999}, {"context_window": 6999},
])
def test_approval_role_context_and_output_limits_are_required(adaptive, monkeypatch, overrides):
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [candidate(**overrides)])
    assert choose().selected_model == settings.AI_MODEL_FAST


@pytest.mark.parametrize("role", ["critic_qa", "vision"])
def test_critical_review_and_ocr_keep_their_static_policy(adaptive, monkeypatch, role):
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [candidate(roles=[role])])
    assert choose(role=role).selected_model == settings.AI_MODEL_FAST


def test_failure_history_and_input_bucket_affect_selection(adaptive):
    history = observations(["contract_fail"] * 3 + ["contract_pass"] * 7)
    assert choose(history=history).selected_model == settings.AI_MODEL_FAST
    assert choose(history=history, input_tokens=9000).selected_model == "test/cheap"
    for row in history:
        row["policy"] = "old-policy"
    assert choose(history=history).selected_model == "test/cheap"


def test_transport_circuit_recovers_after_cooldown(adaptive):
    history = observations(["provider_error"] * 3)
    assert choose(history=history).selected_model == settings.AI_MODEL_FAST
    assert choose(history=history, now=1400).selected_model == "test/cheap"


def test_score_accounts_for_failures_not_only_token_price(adaptive, monkeypatch):
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [
        candidate(input_rub_per_million=10, output_rub_per_million=10),
        candidate("test/reliable", input_rub_per_million=11, output_rub_per_million=11),
    ])
    history = observations(["provider_error"] * 2 + ["contract_pass"] * 8)
    history += observations(["contract_pass"] * 20, selected_model="test/reliable")
    assert choose(history=history).selected_model == "test/reliable"


def test_prices_use_decimal_and_preserve_unknown_usage(adaptive):
    assert routing.estimate_cost(candidate(), 300000, 70000) == Decimal("1.88")
    decision = choose()
    outcome = routing.record_outcome(decision.metadata, outcome="provider_error")
    assert "usage_priced_uncached_rub" not in outcome
    outcome = routing.record_outcome(decision.metadata, outcome="contract_pass",
                                     usage={"input_tokens": 1000, "output_tokens": 500})
    assert Decimal(outcome["usage_priced_uncached_rub"]) == Decimal("0.01")
    assert "outcome" not in decision.metadata


def test_runtime_escalates_after_validation_and_records_requested_model(adaptive, db_session, auth_user):
    calls = []
    def generate(*args, **kwargs):
        calls.append(kwargs)
        return {"value": "invalid" if len(calls) == 1 else 2, "_model": "provider/model-snapshot",
                "_usage_reported": True, "_usage": {"input_tokens": 10, "output_tokens": 5}}
    runtime = make_runtime(db_session, auth_user, generate)
    assert execute(runtime).value == 2
    assert [item["model"] for item in calls] == ["test/cheap", settings.AI_MODEL_FAST]
    assert all(not item["fallback_model"] for item in calls)
    ledger = db_session.query(AICall).order_by(AICall.id).all()
    assert [item.routing["outcome"] for item in ledger] == ["contract_fail", "contract_pass"]
    assert ledger[0].routing["selected_model"] == "test/cheap"
    assert ledger[0].model == "provider/model-snapshot"
    assert ledger[0].input_tokens == 10


def test_owner_history_does_not_change_another_owners_route(adaptive, db_session, auth_user):
    other = User(email="another-routing-owner@example.com", password_hash=auth_user.password_hash)
    db_session.add(other)
    db_session.commit()
    other_runtime = make_runtime(db_session, other, lambda: None)
    for data in observations(["contract_fail"] * 10):
        db_session.add(AICall(run_id=other_runtime.run_id, agent="ingestion", model="test/cheap",
                              status="failed", routing=data))
    db_session.commit()
    seen = []
    runtime = make_runtime(db_session, auth_user,
                           lambda *a, **kw: seen.append(kw["model"]) or {"value": 1})
    execute(runtime)
    assert seen == ["test/cheap"]


def test_same_owner_history_changes_the_route(adaptive, db_session, auth_user):
    runtime = make_runtime(db_session, auth_user, lambda *a, **kw: {"value": 1})
    for data in observations(["contract_fail"] * 10):
        db_session.add(AICall(run_id=runtime.run_id, agent="ingestion", model="test/cheap",
                              status="failed", routing=data))
    db_session.commit()
    execute(runtime)
    call = db_session.query(AICall).order_by(AICall.id.desc()).first()
    assert call.routing["selected_model"] == settings.AI_MODEL_FAST


def test_cache_avoids_paid_calls_and_policy_change_invalidates_it(adaptive, db_session, auth_user, monkeypatch):
    calls = []
    runtime = make_runtime(db_session, auth_user,
                           lambda *a, **kw: calls.append(kw["model"]) or {"value": 1})
    execute(runtime)
    prior = db_session.get(GenerationRun, runtime.run_id)
    prior.status = "failed"
    db_session.commit()
    run = GenerationRun(owner_id=auth_user.id, course_id=runtime.course_id,
                        run_type="graph_generation", status="running")
    db_session.add(run)
    db_session.commit()
    resumed = AgentRuntime(db=db_session, run_id=run.id, course_id=runtime.course_id,
                           generate=runtime.generate)
    execute(resumed)
    assert calls == ["test/cheap"]
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [candidate("test/new")])
    execute(resumed)
    assert calls == ["test/cheap", "test/new"]
    assert db_session.query(AICall).count() == 2


def test_exhausted_token_budget_never_sends_dynamic_call(adaptive, db_session, auth_user, monkeypatch):
    monkeypatch.setattr(settings, "AI_MAX_TOKENS_PER_RUN", 1000)
    runtime = make_runtime(db_session, auth_user, lambda *a, **kw: pytest.fail("network call"))
    with pytest.raises(HTTPException) as exc:
        execute(runtime)
    assert exc.value.status_code == 402
    assert db_session.query(AICall).count() == 0


def test_gateway_enforces_dynamic_allowlist_and_transmits_selected_model(adaptive, monkeypatch):
    calls = []
    class FakeLLM:
        def __init__(self, **kwargs): calls.append(kwargs["model"])
        def bind(self, **kwargs): return self
        def invoke(self, messages):
            return SimpleNamespace(content='{"value": 1}', response_metadata={}, additional_kwargs={},
                                   usage_metadata={"input_tokens": 2, "output_tokens": 4})
    monkeypatch.setattr(gateway, "ChatOpenAI", FakeLLM)
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr("test-only"))
    result = gateway.invoke_messages("ingestion", [{"role": "user", "content": "x"}],
                                     model="test/cheap", json_mode=True)
    assert result["usage_reported"]
    assert calls == ["test/cheap"]
    for role, model, mode in [("assessment", "test/cheap", "adaptive"),
                               ("ingestion", "test/unapproved", "adaptive"),
                               ("ingestion", "test/cheap", "shadow")]:
        monkeypatch.setattr(settings, "AI_ROUTING_MODE", mode)
        with pytest.raises(HTTPException) as exc:
            gateway.invoke_messages(role, [{"role": "user", "content": "x"}], model=model)
        assert exc.value.status_code == 422
    assert calls == ["test/cheap"]


def test_invalid_json_preserves_actual_usage(adaptive, monkeypatch, db_session, auth_user):
    monkeypatch.setattr(gateway, "invoke_messages", lambda *a, **kw: {
        "text": "not JSON", "model": "snapshot", "usage_reported": True,
        "usage": {"input_tokens": 30, "output_tokens": 10, "cached_tokens": 5},
    })
    runtime = make_runtime(db_session, auth_user, generation_service.generate_from_prompt)
    runtime.max_attempts = 1
    with pytest.raises(HTTPException):
        execute(runtime)
    call = db_session.query(AICall).one()
    assert (call.input_tokens, call.output_tokens, call.cached_tokens) == (30, 10, 5)
    assert call.routing["outcome"] == "contract_fail"
    assert Decimal(call.routing["usage_priced_uncached_rub"]) == Decimal("0.00023")


@pytest.mark.parametrize("finish,content,status", [("length", "partial", 502), ("stop", "", 502),
                                                   ("content_filter", "", 422)])
def test_rejected_responses_retain_billed_usage(adaptive, monkeypatch, finish, content, status):
    class FakeLLM:
        def __init__(self, **kwargs): pass
        def invoke(self, messages):
            return SimpleNamespace(content=content, response_metadata={"finish_reason": finish},
                                   additional_kwargs={}, usage_metadata={"input_tokens": 7, "output_tokens": 3})
    monkeypatch.setattr(gateway, "ChatOpenAI", FakeLLM)
    with pytest.raises(HTTPException) as exc:
        gateway.invoke_messages("ingestion", [{"role": "user", "content": "x"}], model="test/cheap")
    assert exc.value.status_code == status
    assert exc.value.ai_usage == {"input_tokens": 7, "output_tokens": 3, "cached_tokens": 0}


def test_usage_route_exposes_decision_only_to_owner(adaptive, client, db_session, auth_user, auth_headers):
    runtime = make_runtime(db_session, auth_user, lambda *a, **kw: {"value": 1})
    execute(runtime)
    response = client.get(f"/api/generation-runs/{runtime.run_id}/usage", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["calls"][0]["routing"]["selected_model"] == "test/cheap"
    other = User(email="private-routing@example.com", password_hash=auth_user.password_hash)
    db_session.add(other)
    db_session.commit()
    other_runtime = make_runtime(db_session, other, lambda: None)
    assert client.get(f"/api/generation-runs/{other_runtime.run_id}/usage", headers=auth_headers).status_code == 404
