from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

# ДОБАВЬ:
try:  # Py3.11+
    from typing import TypedDict
except ImportError:  # Py3.8–3.10
    from typing_extensions import TypedDict


class ChatMessage(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class ChatEngine(Protocol):
    def generate(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        expect_json: bool = False,
        max_tokens: int = 1024,
        **kwargs,
    ) -> dict[str, Any]:
        """Sync answer"""

    def stream(
        self, messages: list[ChatMessage], *, model: str | None = None, **kwargs
    ) -> Iterable[str]:
        """Optional streaming"""
