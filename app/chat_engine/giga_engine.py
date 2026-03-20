from typing import Any

from app.chat_engine.base import ChatEngine, ChatMessage
from app.services.gigachat_service import get_gigachat_client
from app.services.llm_registry import invoke_client


class GigaEngine(ChatEngine):
    def __init__(self, model: str | None = None):
        self.client, self.model = get_gigachat_client(model)

    def generate(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        expect_json: bool = False,
        max_tokens: int = 1024,
        **kwargs,
    ) -> dict[str, Any]:
        text, meta = invoke_client(
            self.client,
            provider="gigachat",
            model=self.model,
            prompt="\n".join([f"{message['role']}: {message['content']}" for message in messages]),
            max_tokens=max_tokens,
        )
        if expect_json:
            import json

            return json.loads(text)
        return {
            "text": text,
            "model": meta.model,
            "provider": meta.provider,
            "latency_ms": meta.latency_ms,
            "attempts": meta.attempts,
        }

    def stream(self, *args, **kwargs):
        raise NotImplementedError
