import logging
import sys
import uuid
from contextvars import ContextVar

from app.core.config import settings


request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or "-"
        return True


def get_logger(name: str = "app") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(request_id)s | %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    logger.addHandler(handler)
    logger.propagate = False
    return logger


def set_request_id(value: str | None) -> str:
    request_id = value or str(uuid.uuid4())
    request_id_ctx.set(request_id)
    return request_id


def clear_request_id() -> None:
    request_id_ctx.set(None)
