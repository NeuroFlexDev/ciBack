# app/chat_engine/lc_engine.py
from __future__ import annotations

import logging  # <-- add

logger = logging.getLogger(__name__)  #  <-- add

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from langchain_core.runnables import Runnable

from app.services.llm_registry import get_llm  # <--- теперь есть


def _lc_messages(history: list[dict[str, str]]) -> list[Any]:
    out: list[Any] = []
    for m in history:
        role = m.get("role")
        if role == "user":
            out.append(HumanMessage(content=m["content"]))
        elif role == "assistant":
            out.append(AIMessage(content=m["content"]))
        elif role == "system":
            out.append(SystemMessage(content=m["content"]))
    return out


class LangChainEngine:
    def __init__(self, model: str | None = None, engine: str | None = None):
        self.llm = get_llm(model=model, engine=engine)
        logger.debug("LangChainEngine init: provider=%s model=%s", engine, model)

        self.prompt: ChatPromptTemplate = ChatPromptTemplate.from_messages(
            [
                SystemMessagePromptTemplate.from_template("Ты помощник. Отвечай кратко и по делу."),
                HumanMessagePromptTemplate.from_template("{question}"),
            ]
        )
        self.chain: Runnable = self.prompt | self.llm | StrOutputParser()

    def generate(
        self,
        history: list[dict[str, str]],
        model: str | None = None,
        expect_json: bool = False,
    ) -> dict[str, Any]:
        question = history[-1]["content"] if history else ""
        text = self.chain.invoke({"question": question})
        return {"text": text}
