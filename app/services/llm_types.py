from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMClient(Protocol):
    def generate(self, prompt: str, max_tokens: int = 1024) -> str: ...


@dataclass(slots=True)
class LLMInvocationMeta:
    provider: str
    model: str
    latency_ms: int = 0
    attempts: int = 1
    token_usage: dict[str, Any] | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class LLMError(Exception):
    code: str
    message: str
    provider: str | None = None
    model: str | None = None
    status_code: int = 503
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def with_context(self, *, provider: str | None = None, model: str | None = None) -> "LLMError":
        if provider and not self.provider:
            self.provider = provider
        if model and not self.model:
            self.model = model
        return self
