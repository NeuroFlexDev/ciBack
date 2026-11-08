from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.ai import control as ai_control
from app.ai import gateway, routing, token_forecast
from app.ai.context_budget import count_messages, require_fits
from app.ai.models import model_policy
from app.ai.routing import RouteContext
from app.core.config import settings
from app.models.agent_artifact import AgentArtifact
from app.models.ai_state import AICall, AIResponseCache
from app.models.generation_run import GenerationRun
from app.repositories.pipeline import PipelineRepository
from app.services.ai_resources import ResourceOutcome, ResourceReservation
from app.services.generation_service import PromptRequest

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
    return (
        gateway.retryable(exc)
        or isinstance(exc, HTTPException)
        and exc.status_code in {429, 500, 502, 503, 504}
    )


def _visit(value, catalog):
    if isinstance(value, list):
        return [_visit(item, catalog) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in {"source_refs", "previous_source_refs", "current_source_refs"} and isinstance(
            child, list
        ):
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
            result[key] = _visit(child, catalog)
    return result


def expand_source_refs(raw: dict, catalog: dict[str, dict]) -> dict:
    """LLMs return IDs; only the server may attach canonical quotes and versions."""

    return _visit(raw, catalog)


@dataclass(frozen=True, kw_only=True)
class AgentInvocation(Generic[ArtifactModel]):
    """Parameters for execute, preserving its original defaults."""

    agent: str
    artifact: str
    sequence: int
    template_name: str
    response_model: type[ArtifactModel]
    prompt_context: dict
    max_tokens: int
    allow_legacy_graph: bool = False
    validator: Callable[[ArtifactModel], None] | None = None
    normalize_response: Callable[[dict], dict] | None = None


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

    def execute(self, *, request: AgentInvocation[ArtifactModel]) -> ArtifactModel:
        return _AgentExecution(self, request).run()


class _AgentExecution(Generic[ArtifactModel]):
    """State of one invocation, including durable receipts and bounded retries."""

    def __init__(self, runtime: AgentRuntime, request: AgentInvocation[ArtifactModel]):
        self.runtime = runtime
        self.request = request

    def _admit_attempt(self):
        if self.live and self.cached is None:
            # Reserve worst-case usage before sending. Failed calls still consume budget.
            from app.services.generation_service import agent_messages, render_prompt

            messages = agent_messages(
                render_prompt(
                    self.request.template_name,
                    **{**self.request.prompt_context, "external_context": ""},
                ),
                expect_json=True,
                repair_feedback=self.repair_feedback,
            )
            baseline_model = self.policy.fallback if self.use_fallback else self.policy.model
            estimate = count_messages(messages, baseline_model)
            request_features = token_forecast.features(
                messages, self.request.agent, self.request.prompt_context
            )
            request_features["is_revision"] = (
                self.request.sequence > 0
                if self.request.agent in {"competency_mapper", "course_architect"}
                else self.request.sequence >= 1000
            )
            from app.ai.providers import route_id

            predictions = {
                route_id(item.provider, item.model): token_forecast.predict(
                    token_forecast.current_model(),
                    role=self.request.agent,
                    model_id=item.model,
                    request_features=request_features,
                    max_output_tokens=self.output_limit,
                )
                for item in routing.enabled_candidates(self.request.agent)
            }
            baseline_model = self.policy.fallback if self.use_fallback else self.policy.model
            predictions[route_id("vsellm", baseline_model)] = token_forecast.predict(
                token_forecast.current_model(),
                role=self.request.agent,
                model_id=baseline_model,
                request_features=request_features,
                max_output_tokens=self.output_limit,
            )
            previous = (
                self.runtime.db.query(AICall)
                .filter(AICall.run_id == self.runtime.run_id, AICall.status != "unavailable")
                .all()
            )
            if any(
                item.agent == self.request.agent
                and item.status in {"reserved", "received"}
                and (item.routing or {}).get("observation", {}).get("sequence")
                == self.request.sequence
                for item in previous
            ):
                # A worker died between dispatch and durable validation.
                # Checkpoint resume must not silently repeat a paid call.
                error = HTTPException(
                    409,
                    {
                        "code": "ai_result_unknown",
                        "message": "Результат предыдущего AI-запроса неизвестен. Автоматический платный повтор остановлен.",
                    },
                )
                error.request_sent = False
                raise error
            consumed = sum(item.input_tokens + item.output_tokens for item in previous)
            if ai_control.policy()["mode"] != "off":
                history = (
                    self.runtime.db.query(AICall)
                    .join(GenerationRun)
                    .filter(
                        GenerationRun.owner_id == self.runtime.owner_id,
                        AICall.agent == self.request.agent,
                        AICall.routing.isnot(None),
                        AICall.status.in_(["completed", "failed"]),
                    )
                    .order_by(AICall.id.desc())
                    .limit(settings.AI_ROUTING_HISTORY_LIMIT)
                    .all()
                )
                self.decision = routing.choose_route(
                    role=self.request.agent,
                    baseline_model=self.policy.fallback if self.use_fallback else self.policy.model,
                    input_tokens=estimate,
                    output_tokens=self.output_limit,
                    routing_context=RouteContext(
                        history=[item.routing for item in history if item.routing],
                        excluded_models=self.excluded_models,
                        min_tier=self.min_tier,
                        available_providers=gateway.available_provider_ids(),
                        forecast=predictions,
                        input_estimates={
                            route_id(item.provider, item.model): count_messages(
                                messages, item.model
                            )
                            for item in routing.enabled_candidates(self.request.agent)
                        },
                    ),
                )
            selected_model = (
                self.decision.selected_model
                if self.decision
                else (self.policy.fallback if self.use_fallback else self.policy.model)
            )
            selected_provider = self.decision.selected_provider if self.decision else "vsellm"
            estimate = require_fits(messages, selected_model, self.output_limit, selected_provider)
            from app.services.ai_entitlements import unlimited_ai
            from app.services.ai_resources import scope

            if not unlimited_ai(
                self.runtime.db,
                scope(self.runtime.db, self.runtime.owner_id, self.runtime.course_id),
            ) and (
                len(previous) >= settings.AI_MAX_CALLS_PER_RUN
                or consumed + estimate + self.output_limit > settings.AI_MAX_TOKENS_PER_RUN
            ):
                raise HTTPException(402, "AI run budget exhausted")
            metadata = (
                self.decision.metadata
                if self.decision
                else {
                    "selected_model": selected_model,
                    "selected_provider": selected_provider,
                }
            )
            metadata = {
                **metadata,
                "forecast": token_forecast.predict(
                    token_forecast.current_model(),
                    role=self.request.agent,
                    model_id=selected_model,
                    request_features=request_features,
                    max_output_tokens=self.output_limit,
                ),
                "observation": {
                    "features": request_features,
                    "attempt": self.attempt,
                    "sequence": self.request.sequence,
                    "max_output_tokens": self.output_limit,
                    "usage_reported": False,
                },
            }
            self.call = AICall(
                run_id=self.runtime.run_id,
                agent=self.request.agent,
                model=selected_model,
                routing=metadata,
                status="reserved",
                input_tokens=estimate,
                output_tokens=self.output_limit,
            )
            self.runtime.db.add(self.call)
            self.runtime.db.commit()
            from app.services.ai_resources import reserve, text_price

            self.resource = reserve(
                self.runtime.db,
                reservation=ResourceReservation(
                    owner_id=self.runtime.owner_id,
                    course_id=self.runtime.course_id,
                    run_id=self.runtime.run_id,
                    operation="generation_text",
                    tokens=estimate + self.output_limit,
                    cost=text_price(
                        self.request.agent,
                        estimate,
                        self.output_limit,
                        model=selected_model,
                        provider=selected_provider,
                    ),
                ),
            )
            self.runtime.db.commit()

    def _record_provider_receipt(self):
        self.used_model = self.raw.pop("_model", None) if isinstance(self.raw, dict) else None
        self.usage = self.raw.pop("_usage", {}) if isinstance(self.raw, dict) else {}
        self.usage_reported = (
            self.raw.pop("_usage_reported", False) if isinstance(self.raw, dict) else False
        )
        if self.resource:
            from app.services.ai_resources import finish

            finish(
                self.runtime.db,
                self.resource,
                outcome=ResourceOutcome(usage=self.usage if self.usage_reported else None),
            )
            self.resource = None
        (self.raw.pop("_provider", "vsellm") if isinstance(self.raw, dict) else "vsellm")
        if self.usage_reported:
            self.reported_usage = self.usage
        if self.call:
            self.call.status = "received"
            self.call.model = self.used_model or self.call.model
            self.call.input_tokens = self.usage.get("input_tokens", self.call.input_tokens)
            self.call.output_tokens = self.usage.get("output_tokens", self.call.output_tokens)
            self.call.cached_tokens = self.usage.get("cached_tokens", 0)
            self.call.routing = {
                **self.call.routing,
                "observation": {
                    **self.call.routing["observation"],
                    "usage_reported": self.usage_reported,
                },
            }
            self.runtime.db.commit()

    def _validate_result(self):
        if self.runtime.source_catalog:
            if self.request.normalize_response is not None:
                self.raw = self.request.normalize_response(self.raw)
            self.raw = expand_source_refs(self.raw, self.runtime.source_catalog)
        self.result = self.request.response_model.model_validate(self.raw)
        if self.request.validator:
            self.request.validator(self.result)

    def _store_result(self):
        latency_ms = max(0, int((time.perf_counter() - self.started) * 1000))
        if self.stored is None:
            self.stored = AgentArtifact(
                run_id=self.runtime.run_id,
                course_id=self.runtime.course_id,
                agent=self.request.agent,
                artifact=self.request.artifact,
                sequence=self.request.sequence,
            )
            PipelineRepository.add_agent_artifact(self.runtime.db, self.stored)
        self.stored.status = "completed"
        self.stored.schema_version = 1
        self.stored.payload = self.result.model_dump(mode="json")
        self.stored.input_fingerprint = self.input_fingerprint
        self.stored.model = self.used_model
        self.stored.latency_ms = latency_ms
        self.stored.error = None
        if self.call:
            self.call.status = "completed"
            self.call.latency_ms = max(0, int((time.perf_counter() - self.attempt_started) * 1000))
            self.call.routing = routing.record_outcome(
                self.call.routing, outcome="contract_pass", usage=self.reported_usage
            )
        if self.live and self.cached is None and settings.AI_CACHE_TTL_SECONDS:
            self.runtime.db.merge(
                AIResponseCache(
                    key=self.cache_key,
                    owner_id=self.runtime.owner_id,
                    course_id=self.runtime.course_id,
                    model=self.used_model or self.policy.model,
                    payload={**self.result.model_dump(mode="json"), "_model": self.used_model},
                    expires_at=datetime.utcnow() + timedelta(seconds=settings.AI_CACHE_TTL_SECONDS),
                )
            )
        self.runtime.db.commit()

    @staticmethod
    def _failure_outcome(exc):
        failure_kind = getattr(exc, "ai_failure_kind", None)
        if failure_kind == "truncated":
            outcome = "output_truncated"
        elif isinstance(exc, (ValidationError, ValueError)) or failure_kind in {
            "invalid_json",
            "empty",
        }:
            outcome = "contract_fail"
        elif gateway.retryable(exc):
            outcome = "provider_error"
        else:
            outcome = "runtime_error"
        return outcome

    def _record_failure(self, exc):
        if self.resource:
            from app.services.ai_resources import finish

            finish(
                self.runtime.db,
                self.resource,
                outcome=ResourceOutcome(
                    usage=getattr(exc, "ai_usage", None),
                    unsent=getattr(exc, "request_sent", None) is False,
                    failed=True,
                ),
            )
        if self.call:
            self.call.status = "failed"
            self.usage = getattr(exc, "ai_usage", {})
            if getattr(exc, "request_sent", None) is False:
                self.call.status = "unavailable"
                self.usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
            self.call.input_tokens = self.usage.get("input_tokens", self.call.input_tokens)
            self.call.output_tokens = self.usage.get("output_tokens", self.call.output_tokens)
            self.call.cached_tokens = self.usage.get("cached_tokens", self.call.cached_tokens)
            self.call.model = getattr(exc, "ai_model", None) or self.call.model
            if getattr(exc, "ai_usage", None):
                self.call.routing = {
                    **self.call.routing,
                    "observation": {**self.call.routing["observation"], "usage_reported": True},
                }
            outcome = self._failure_outcome(exc)
            self.call.routing = routing.record_outcome(
                self.call.routing,
                outcome=outcome,
                usage=getattr(exc, "ai_usage", None) or self.reported_usage,
            )
            if self.decision and ai_control.policy()["mode"] == "adaptive":
                from app.ai.providers import route_id

                if outcome != "output_truncated":
                    self.excluded_models.add(
                        route_id(self.decision.selected_provider, self.decision.selected_model)
                    )
                tier = self.decision.metadata.get("tier")
                if tier is not None and outcome == "contract_fail":
                    self.min_tier = max(self.min_tier, tier + 1)
            self.call.latency_ms = max(0, int((time.perf_counter() - self.attempt_started) * 1000))
            self.call.error_code = gateway.safe_error(exc)
            if isinstance(exc, ValidationError):
                self.call.error_code = (
                    "validation:"
                    + ",".join(
                        ".".join(map(str, e["loc"])) + ":" + e["type"]
                        for e in exc.errors(include_input=False, include_url=False)
                    )
                )[:128]
                # Contract diagnostics exclude raw model responses, source
                # contents and Pydantic context objects.
                self.call.routing = {
                    **self.call.routing,
                    "validation_errors": [
                        {
                            "loc": list(e["loc"]),
                            "type": e["type"],
                            "message": e["msg"][:600],
                        }
                        for e in exc.errors(
                            include_input=False, include_context=False, include_url=False
                        )[:8]
                    ],
                }
            self.runtime.db.add(self.call)
            self.runtime.db.commit()

    def _prepare_repair(self, exc):
        if getattr(exc, "ai_failure_kind", None) == "truncated":
            self.output_limit = min(self.initial_output_limit * 4, self.output_limit * 2)
            self.repair_feedback = (
                "The previous response reached its output-token limit. More output room is now reserved. "
                "Return complete compact JSON; do not repeat source objects or explanations. Preserve all assigned IDs and objectives."
            )
        elif isinstance(exc, ValidationError):
            self.repair_feedback = json.dumps(
                exc.errors(include_input=False, include_url=False), default=str
            )[:5000]
        elif isinstance(exc, ValueError):
            self.repair_feedback = str(exc)[:3000]
        else:
            self.repair_feedback = "Return complete valid JSON matching the requested contract."

    def _store_terminal_failure(self):
        latency_ms = max(0, int((time.perf_counter() - self.started) * 1000))
        safe_error = gateway.safe_error(self.last_error) if self.last_error else "Agent failed"
        if self.stored is None:
            self.stored = AgentArtifact(
                run_id=self.runtime.run_id,
                course_id=self.runtime.course_id,
                agent=self.request.agent,
                artifact=self.request.artifact,
                sequence=self.request.sequence,
            )
            PipelineRepository.add_agent_artifact(self.runtime.db, self.stored)
        self.stored.status = "failed"
        self.stored.input_fingerprint = self.input_fingerprint
        self.stored.latency_ms = latency_ms
        self.stored.error = safe_error
        self.runtime.db.commit()

    def _prepare_checkpoint(self):
        self.policy = model_policy(self.request.agent)
        self.live = gateway.configured() or settings.ENV != "test"
        self.output_limit = self.policy.max_output_tokens if self.live else self.request.max_tokens
        self.input_fingerprint = _fingerprint(
            self.request.template_name,
            self.request.response_model,
            {
                **self.request.prompt_context,
                "_models": [self.policy.model, self.policy.fallback],
                "_max_tokens": self.output_limit,
                **routing.cache_policy(),
            },
        )
        self.cache_key = hashlib.sha256(
            f"{self.runtime.owner_id}:{self.runtime.course_id}:{self.input_fingerprint}".encode()
        ).hexdigest()
        self.initial_output_limit = self.output_limit
        # A confirmed length stop is a known billed result. Repeating the same
        # response ceiling cannot repair it. Carry the required headroom across
        # checkpoint retries; admission still checks price and model capacity.
        for previous in self.runtime.db.query(AICall).filter_by(
            run_id=self.runtime.run_id, agent=self.request.agent, error_code="ai_output_truncated"
        ):
            observation = (previous.routing or {}).get("observation", {})
            if observation.get("sequence") == self.request.sequence:
                self.output_limit = max(
                    self.output_limit,
                    min(
                        self.initial_output_limit * 4,
                        int(observation.get("max_output_tokens", self.initial_output_limit)) * 2,
                    ),
                )
        self.stored = PipelineRepository.get_agent_artifact(
            self.runtime.db,
            run_id=self.runtime.run_id,
            agent=self.request.agent,
            sequence=self.request.sequence,
        )

    def _load_cached_response(self):
        if self.live and settings.AI_CACHE_TTL_SECONDS:
            self.runtime.db.query(AIResponseCache).filter(
                AIResponseCache.expires_at <= datetime.utcnow()
            ).delete(synchronize_session="fetch")
            self.runtime.db.commit()
        self.cached = (
            self.runtime.db.get(AIResponseCache, self.cache_key)
            if self.live and settings.AI_CACHE_TTL_SECONDS
            else None
        )

    def _generate_response(self):
        self.raw = (
            copy.deepcopy(self.cached.payload)
            if self.cached
            else self.runtime.generate(
                self.request.template_name,
                request=PromptRequest(
                    include_external_context=False,
                    use_feedback=False,
                    expect_json=True,
                    max_tokens=self.output_limit,
                    agent_role=self.request.agent,
                    fallback_model=self.use_fallback if self.decision is None else False,
                    repair_feedback=self.repair_feedback,
                    model=self.decision.selected_model if self.decision else None,
                    provider=self.decision.selected_provider if self.decision else None,
                ),
                **self.request.prompt_context,
            )
        )
        if (
            self.request.allow_legacy_graph
            and isinstance(self.raw, dict)
            and isinstance(self.raw.get("nodes"), list)
            and isinstance(self.raw.get("edges"), list)
        ):
            raise LegacyGraphResponse(self.raw)

    def run(self) -> ArtifactModel:
        from app.services.generation_control import check_cancelled

        check_cancelled(self.runtime.db, self.runtime.run_id)
        self._prepare_checkpoint()
        if (
            self.stored is not None
            and self.stored.status == "completed"
            and self.stored.input_fingerprint == self.input_fingerprint
        ):
            try:
                self.result = self.request.response_model.model_validate(self.stored.payload)
                if self.request.validator:
                    self.request.validator(self.result)
                return self.result
            except (ValidationError, ValueError):
                # A strengthened contract may invalidate an older checkpoint.
                pass

        self._load_cached_response()
        self.last_error: Exception | None = None
        self.started = time.perf_counter()
        self.repair_feedback = ""
        self.excluded_models: set[str] = set()
        self.min_tier = 0
        for attempt in range(1, self.runtime.max_attempts + 1):
            self.attempt = attempt
            check_cancelled(self.runtime.db, self.runtime.run_id)
            self.attempt_started = time.perf_counter()
            self.use_fallback = self.attempt > 1 and (
                self.attempt == self.runtime.max_attempts
                or not isinstance(self.last_error, (ValidationError, ValueError))
            )
            self.call = None
            self.resource = None
            self.decision = None
            self.reported_usage = None
            try:
                self._admit_attempt()
                self._generate_response()
                self._record_provider_receipt()
                self._validate_result()
                self._store_result()
                return self.result
            except Exception as exc:
                if isinstance(exc, LegacyGraphResponse):
                    raise
                response_ceiling_exhausted = (
                    getattr(exc, "ai_failure_kind", None) == "truncated"
                    and self.output_limit >= self.initial_output_limit * 4
                )
                self.last_error = exc
                self.runtime.db.rollback()
                self._record_failure(exc)
                self._prepare_repair(exc)
                if self.cached:
                    self.runtime.db.delete(self.cached)
                    self.runtime.db.commit()
                    self.cached = None
                self.stored = PipelineRepository.get_agent_artifact(
                    self.runtime.db,
                    run_id=self.runtime.run_id,
                    agent=self.request.agent,
                    sequence=self.request.sequence,
                )
                if (
                    self.attempt == self.runtime.max_attempts
                    or response_ceiling_exhausted
                    or not _retryable(exc)
                ):
                    break
                if self.live and not isinstance(exc, (ValidationError, ValueError)):
                    time.sleep(min(2 ** (self.attempt - 1), 8))

        self._store_terminal_failure()
        assert self.last_error is not None
        raise self.last_error
