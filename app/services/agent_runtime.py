from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.agent_artifact import AgentArtifact
from app.repositories.pipeline import PipelineRepository


ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)


class LegacyGraphResponse(Exception):
    def __init__(self, payload: dict):
        super().__init__("legacy graph response")
        self.payload = payload


def _fingerprint(template_name: str, response_model: type[BaseModel], data: dict) -> str:
    encoded = json.dumps(
        {
            "template": template_name,
            "schema": response_model.__name__,
            "schema_version": 1,
            "input": data,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, ValidationError, ValueError)):
        return True
    return isinstance(exc, HTTPException) and exc.status_code in {429, 500, 502, 503, 504}


class AgentRuntime:
    """Typed, persisted execution boundary shared by all course agents."""

    def __init__(
        self,
        *,
        db: Session,
        run_id: int,
        course_id: int,
        generate: Callable[..., dict],
        max_attempts: int = 2,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.course_id = course_id
        self.generate = generate
        self.max_attempts = max_attempts

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
    ) -> ArtifactModel:
        input_fingerprint = _fingerprint(template_name, response_model, prompt_context)
        stored = PipelineRepository.get_agent_artifact(
            self.db, run_id=self.run_id, agent=agent, sequence=sequence
        )
        if (
            stored is not None
            and stored.status == "completed"
            and stored.input_fingerprint == input_fingerprint
        ):
            return response_model.model_validate(stored.payload)

        last_error: Exception | None = None
        started = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.generate(
                    template_name,
                    include_external_context=False,
                    use_feedback=False,
                    expect_json=True,
                    max_tokens=max_tokens,
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
                result = response_model.model_validate(raw)
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
                self.db.commit()
                return result
            except Exception as exc:
                if isinstance(exc, LegacyGraphResponse):
                    raise
                last_error = exc
                self.db.rollback()
                stored = PipelineRepository.get_agent_artifact(
                    self.db, run_id=self.run_id, agent=agent, sequence=sequence
                )
                if attempt == self.max_attempts or not _retryable(exc):
                    break

        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        safe_error = (str(last_error).strip() if last_error else "Agent failed")[:2000]
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
