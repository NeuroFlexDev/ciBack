# Унифицированный интерфейс для клиентов LLM
from typing import Protocol


class LLMClient(Protocol):
    def generate(self, prompt: str, max_tokens: int = 1024) -> str: ...
