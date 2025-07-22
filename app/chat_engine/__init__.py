# app/chat_engine/__init__.py
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Dict, Optional

from app.chat_engine.base import ChatEngine
from app.chat_engine.giga_engine import GigaEngine
from app.chat_engine.hf_engine import HfEngine
from app.chat_engine.lc_engine import LangChainEngine

logger = logging.getLogger(__name__)

ENGINES: dict[str, Callable[[str | None], ChatEngine]] = {
    # «сырые» вызовы
    "raw_hf": lambda m: HfEngine(m),
    "raw_giga": lambda m: GigaEngine(m),
    # LangChain + HuggingFace
    "lc_hf": lambda m: LangChainEngine(model=m, engine="hf_api"),
    # LangChain + GigaChat
    "lc_giga": lambda m: LangChainEngine(model=m, engine="gigachat"),
}


def get_chat_engine(name: str, model: str | None) -> ChatEngine:
    if name not in ENGINES:
        raise ValueError(f"Unknown engine {name!r}")
    logger.debug("get_chat_engine: name=%s model=%s", name, model)
    return ENGINES[name](model)
