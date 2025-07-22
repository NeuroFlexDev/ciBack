from typing import Any

from app.chat_engine.base import ChatEngine, ChatMessage
from app.services.hf_infer_service import get_hf_client


class HfEngine(ChatEngine):
    def __init__(self, model: str | None = None):
        self.client, self.model = get_hf_client(model)

    def generate(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        expect_json: bool = False,
        max_tokens: int = 1024,
        **kwargs,
    ) -> dict[str, Any]:
        text = self.client.generate(
            "\n".join([f"{m['role']}: {m['content']}" for m in messages]),
            max_tokens=max_tokens,
        )
        if expect_json:
            import json

            return json.loads(text)
        return {"text": text, "model": self.model}

    def stream(self, *args, **kwargs):
        raise NotImplementedError
