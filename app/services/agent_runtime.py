from __future__ import annotations

import hashlib
import json
import time
import copy
from datetime import datetime, timedelta
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.agent_artifact import AgentArtifact
from app.repositories.pipeline import PipelineRepository
from app.models.generation_run import GenerationRun
from app.models.ai_state import AICall, AIResponseCache
from app.core.config import settings
from app.ai import gateway, routing, token_forecast
from app.ai.models import model_policy
from app.ai.context_budget import count_messages, require_fits


ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)


class LegacyGraphResponse(Exception):
    def __init__(self, payload: dict):
        super().__init__("legacy graph response")
        self.payload = payload


def _fingerprint(template_name: str, response_model: type[BaseModel], data: dict) -> str:
    from app.services.generation_service import env
    try:
        template_source = env.loader.get_source(env, template_name)[0]
    except Exception:
        template_source = template_name
    encoded = json.dumps(
        {
            "template": template_name,
            "template_source": template_source,
            "schema": response_model.model_json_schema(),
            "schema_version": 2,
            "input": data,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _retryable(exc: Exception) -> bool:
    from app.services.ai_resources import CapacityBusy
    if isinstance(exc, CapacityBusy) or getattr(exc, "request_sent", None) is True:
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, ValidationError, ValueError)):
        return True
    return gateway.retryable(exc) or isinstance(exc, HTTPException) and exc.status_code in {429, 500, 502, 503, 504}


def expand_source_refs(raw: dict, catalog: dict[str, dict]) -> dict:
    """LLMs return IDs; only the server may attach canonical quotes and versions."""
    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, child in value.items():
            if key in {"source_refs", "previous_source_refs", "current_source_refs"} and isinstance(child, list):
                refs = []
                for item in child:
                    if not isinstance(item, (str, dict)):
                        raise ValueError("Source references must be existing ID strings")
                    ref_id = item if isinstance(item, str) else item.get("id")
                    if not isinstance(ref_id, str):
                        raise ValueError("A source reference ID must be a string")
                    canonical = catalog.get(ref_id)
                    if canonical is None:
                        raise ValueError(f"Unknown source ref: {ref_id}")
                    if isinstance(item, dict) and item != canonical:
                        raise ValueError(f"Changed source ref: {ref_id}")
                    refs.append(copy.deepcopy(canonical))
                result[key] = refs
            else:
                result[key] = visit(child)
        return result
    return visit(raw)


class AgentRuntime:
    """Typed, persisted execution boundary shared by all course agents."""

    def __init__(
        self,
        *,
        db: Session,
        run_id: int,
        course_id: int,
        generate: Callable[..., dict],
        max_attempts: int | None = None,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.course_id = course_id
        self.generate = generate
        self.max_attempts = max_attempts or settings.AI_MAX_ATTEMPTS
        self.source_catalog: dict[str, dict] = {}
        run = db.get(GenerationRun, run_id)
        self.owner_id = run.owner_id if run else None

    def register_sources(self, sources: list[dict]) -> None:
        self.source_catalog.update({item["id"]: item for item in sources})

    def execute(
        self,
        *,
        agent: str,
        artifact: str,
        sequence: int,
        template_name: str,
        response_model: type[ArtifactModel],
        prompt_context: dict,
        max_tokens: int,
        allow_legacy_graph: bool = False,
        validator: Callable[[ArtifactModel], None] | None = None,
        normalize_response: Callable[[dict], dict] | None = None,
    ) -> ArtifactModel:
        from app.services.generation_control import check_cancelled
        check_cancelled(self.db, self.run_id)
        policy = model_policy(agent)
        live = gateway.configured() or settings.ENV != "test"
        output_limit = policy.max_output_tokens if live else max_tokens
        input_fingerprint = _fingerprint(template_name, response_model, {
            **prompt_context, "_models": [policy.model, policy.fallback], "_max_tokens": output_limit,
            **routing.cache_policy(),
        })
        cache_key = hashlib.sha256(f"{self.owner_id}:{self.course_id}:{input_fingerprint}".encode()).hexdigest()
        stored = PipelineRepository.get_agent_artifact(
            self.db, run_id=self.run_id, agent=agent, sequence=sequence
        )
        if (
            stored is not None
            and stored.status == "completed"
            and stored.input_fingerprint == input_fingerprint
        ):
            try:
                result = response_model.model_validate(stored.payload)
                if validator:
                    validator(result)
                return result
            except (ValidationError, ValueError):
                # A strengthened contract may invalidate an older checkpoint.
                pass

        if live and settings.AI_CACHE_TTL_SECONDS:
            self.db.query(AIResponseCache).filter(AIResponseCache.expires_at <= datetime.utcnow()).delete(synchronize_session="fetch")
            self.db.commit()
        cached = self.db.get(AIResponseCache, cache_key) if live and settings.AI_CACHE_TTL_SECONDS else None

        last_error: Exception | None = None
        started = time.perf_counter()
        repair_feedback = ""
        excluded_models: set[str] = set()
        min_tier = 0
        for attempt in range(1, self.max_attempts + 1):
            check_cancelled(self.db, self.run_id)
            attempt_started = time.perf_counter()
            use_fallback = attempt > 1 and (attempt == self.max_attempts or not isinstance(last_error, (ValidationError, ValueError)))
            call = None
            resource = None
            decision = None
            reported_usage = None
            try:
                if live and cached is None:
                    # Reserve worst-case usage before sending. Failed calls still consume budget.
                    from app.services.generation_service import render_prompt, agent_messages
                    messages = agent_messages(render_prompt(template_name, **{**prompt_context, "external_context": ""}),
                                              expect_json=True, repair_feedback=repair_feedback)
                    baseline_model = policy.fallback if use_fallback else policy.model
                    estimate = count_messages(messages, baseline_model)
                    request_features = token_forecast.features(messages, agent, prompt_context)
                    request_features["is_revision"] = (sequence > 0 if agent in {"competency_mapper", "course_architect"}
                                                       else sequence >= 1000)
                    from app.ai.providers import route_id
                    predictions = {route_id(item.provider, item.model): token_forecast.predict(
                        token_forecast.current_model(), role=agent, model_id=item.model,
                        request_features=request_features, max_output_tokens=output_limit,
                    ) for item in routing.enabled_candidates(agent)}
                    baseline_model = policy.fallback if use_fallback else policy.model
                    predictions[route_id("vsellm", baseline_model)] = token_forecast.predict(
                        token_forecast.current_model(), role=agent, model_id=baseline_model,
                        request_features=request_features, max_output_tokens=output_limit)
                    previous = self.db.query(AICall).filter(AICall.run_id == self.run_id,
                                                          AICall.status != "unavailable").all()
                    if any(item.agent == agent and item.status in {"reserved", "received"} and
                           (item.routing or {}).get("observation", {}).get("sequence") == sequence for item in previous):
                        # A worker died between dispatch and durable validation.
                        # Checkpoint resume must not silently repeat a paid call.
                        error = HTTPException(409, {"code": "ai_result_unknown", "message": "Результат предыдущего AI-запроса неизвестен. Автоматический платный повтор остановлен."})
                        error.request_sent = False
                        raise error
                    consumed = sum(item.input_tokens + item.output_tokens for item in previous)
                    if settings.AI_ROUTING_MODE != "off":
                        history = self.db.query(AICall).join(GenerationRun).filter(
                            GenerationRun.owner_id == self.owner_id, AICall.agent == agent,
                            AICall.routing.isnot(None),
                            AICall.status.in_(["completed", "failed"]),
                        ).order_by(AICall.id.desc()).limit(settings.AI_ROUTING_HISTORY_LIMIT).all()
                        decision = routing.choose_route(
                            role=agent, baseline_model=policy.fallback if use_fallback else policy.model,
                            input_tokens=estimate, output_tokens=output_limit,
                            history=[item.routing for item in history if item.routing],
                            excluded_models=excluded_models, min_tier=min_tier,
                            available_providers=gateway.available_provider_ids(),
                            forecast=predictions,
                            input_estimates={route_id(item.provider, item.model): count_messages(messages, item.model) for item in routing.enabled_candidates(agent)},
                        )
                    selected_model = decision.selected_model if decision else (policy.fallback if use_fallback else policy.model)
                    selected_provider = decision.selected_provider if decision else "vsellm"
                    estimate = require_fits(messages, selected_model, output_limit, selected_provider)
                    if len(previous) >= settings.AI_MAX_CALLS_PER_RUN or consumed + estimate + output_limit > settings.AI_MAX_TOKENS_PER_RUN:
                        raise HTTPException(402, "AI run budget exhausted")
                    metadata = decision.metadata if decision else {"selected_model": selected_model, "selected_provider": selected_provider}
                    metadata = {**metadata, "forecast": token_forecast.predict(token_forecast.current_model(),
                        role=agent, model_id=selected_model, request_features=request_features, max_output_tokens=output_limit),
                        "observation": {"features": request_features, "attempt": attempt, "sequence": sequence,
                                        "max_output_tokens": output_limit, "usage_reported": False}}
                    call = AICall(run_id=self.run_id, agent=agent,
                                  model=selected_model, routing=metadata,
                                  status="reserved", input_tokens=estimate, output_tokens=output_limit)
                    self.db.add(call)
                    self.db.commit()
                    from app.services.ai_resources import reserve, text_price
                    resource = reserve(self.db, owner_id=self.owner_id, course_id=self.course_id,
                        run_id=self.run_id, operation="generation_text", tokens=estimate + output_limit,
                        cost=text_price(agent, estimate, output_limit, model=selected_model, provider=selected_provider))
                    self.db.commit()
                raw = copy.deepcopy(cached.payload) if cached else self.generate(
                    template_name,
                    include_external_context=False,
                    use_feedback=False,
                    expect_json=True,
                    max_tokens=output_limit,
                    agent_role=agent,
                    fallback_model=use_fallback if decision is None else False,
                    repair_feedback=repair_feedback,
                    **({"model": decision.selected_model, "provider": decision.selected_provider} if decision else {}),
                    **prompt_context,
                )
                if (
                    allow_legacy_graph
                    and isinstance(raw, dict)
                    and isinstance(raw.get("nodes"), list)
                    and isinstance(raw.get("edges"), list)
                ):
                    raise LegacyGraphResponse(raw)
                used_model = raw.pop("_model", None) if isinstance(raw, dict) else None
                usage = raw.pop("_usage", {}) if isinstance(raw, dict) else {}
                usage_reported = raw.pop("_usage_reported", False) if isinstance(raw, dict) else False
                if resource:
                    from app.services.ai_resources import finish
                    finish(self.db, resource, usage=usage if usage_reported else None)
                    resource = None
                used_provider = raw.pop("_provider", "vsellm") if isinstance(raw, dict) else "vsellm"
                if usage_reported:
                    reported_usage = usage
                if call:
                    call.status = "received"
                    call.model = used_model or call.model
                    call.input_tokens = usage.get("input_tokens", call.input_tokens)
                    call.output_tokens = usage.get("output_tokens", call.output_tokens)
                    call.cached_tokens = usage.get("cached_tokens", 0)
                    call.routing = {**call.routing, "observation": {**call.routing["observation"],
                                                                   "usage_reported": usage_reported}}
                    self.db.commit()
                if self.source_catalog:
                    if normalize_response is not None:
                        raw = normalize_response(raw)
                    raw = expand_source_refs(raw, self.source_catalog)
                result = response_model.model_validate(raw)
                if validator:
                    validator(result)
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                if stored is None:
                    stored = AgentArtifact(
                        run_id=self.run_id,
                        course_id=self.course_id,
                        agent=agent,
                        artifact=artifact,
                        sequence=sequence,
                    )
                    PipelineRepository.add_agent_artifact(self.db, stored)
                stored.status = "completed"
                stored.schema_version = 1
                stored.payload = result.model_dump(mode="json")
                stored.input_fingerprint = input_fingerprint
                stored.model = used_model
                stored.latency_ms = latency_ms
                stored.error = None
                if call:
                    call.status = "completed"
                    call.latency_ms = max(0, int((time.perf_counter() - attempt_started) * 1000))
                    call.routing = routing.record_outcome(call.routing, outcome="contract_pass", usage=reported_usage)
                if live and cached is None and settings.AI_CACHE_TTL_SECONDS:
                    self.db.merge(AIResponseCache(key=cache_key, owner_id=self.owner_id,
                        course_id=self.course_id, model=used_model or policy.model,
                        payload={**result.model_dump(mode="json"), "_model": used_model},
                        expires_at=datetime.utcnow() + timedelta(seconds=settings.AI_CACHE_TTL_SECONDS)))
                self.db.commit()
                return result
            except Exception as exc:
                if isinstance(exc, LegacyGraphResponse):
                    raise
                last_error = exc
                self.db.rollback()
                if resource:
                    from app.services.ai_resources import finish
                    finish(self.db, resource, usage=getattr(exc, "ai_usage", None),
                        unsent=getattr(exc, "request_sent", None) is False, failed=True)
                if call:
                    call.status = "failed"
                    usage = getattr(exc, "ai_usage", {})
                    if getattr(exc, "request_sent", None) is False:
                        call.status = "unavailable"
                        usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
                    call.input_tokens = usage.get("input_tokens", call.input_tokens)
                    call.output_tokens = usage.get("output_tokens", call.output_tokens)
                    call.cached_tokens = usage.get("cached_tokens", call.cached_tokens)
                    call.model = getattr(exc, "ai_model", None) or call.model
                    if getattr(exc, "ai_usage", None):
                        call.routing = {**call.routing, "observation": {**call.routing["observation"], "usage_reported": True}}
                    failure_kind = getattr(exc, "ai_failure_kind", None)
                    if isinstance(exc, (ValidationError, ValueError)) or failure_kind in {"invalid_json", "truncated", "empty"}:
                        outcome = "contract_fail"
                    elif gateway.retryable(exc):
                        outcome = "provider_error"
                    else:
                        outcome = "runtime_error"
                    call.routing = routing.record_outcome(call.routing, outcome=outcome,
                                                         usage=getattr(exc, "ai_usage", None) or reported_usage)
                    if decision and settings.AI_ROUTING_MODE == "adaptive":
                        from app.ai.providers import route_id
                        excluded_models.add(route_id(decision.selected_provider, decision.selected_model))
                        tier = decision.metadata.get("tier")
                        if tier is not None and outcome == "contract_fail":
                            min_tier = max(min_tier, tier + 1)
                    call.latency_ms = max(0, int((time.perf_counter() - attempt_started) * 1000))
                    call.error_code = gateway.safe_error(exc)
                    if isinstance(exc, ValidationError):
                        call.error_code = ("validation:" + ",".join(".".join(map(str,e["loc"])) + ":" + e["type"] for e in exc.errors(include_input=False,include_url=False)))[:128]
                    self.db.add(call)
                    self.db.commit()
                if isinstance(exc, ValidationError):
                    repair_feedback = json.dumps(exc.errors(include_input=False, include_url=False), default=str)[:5000]
                elif isinstance(exc, ValueError):
                    repair_feedback = str(exc)[:3000]
                else:
                    repair_feedback = "Return complete valid JSON matching the requested contract."
                if cached:
                    self.db.delete(cached)
                    self.db.commit()
                    cached = None
                stored = PipelineRepository.get_agent_artifact(
                    self.db, run_id=self.run_id, agent=agent, sequence=sequence
                )
                if attempt == self.max_attempts or not _retryable(exc):
                    break
                if live and not isinstance(exc, (ValidationError, ValueError)):
                    time.sleep(min(2 ** (attempt - 1), 8))

        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        safe_error = gateway.safe_error(last_error) if last_error else "Agent failed"
        if stored is None:
            stored = AgentArtifact(
                run_id=self.run_id,
                course_id=self.course_id,
                agent=agent,
                artifact=artifact,
                sequence=sequence,
            )
            PipelineRepository.add_agent_artifact(self.db, stored)
        stored.status = "failed"
        stored.input_fingerprint = input_fingerprint
        stored.latency_ms = latency_ms
        stored.error = safe_error
        self.db.commit()
        assert last_error is not None
        raise last_error
