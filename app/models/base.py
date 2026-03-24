from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class BaseModelMixin:
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
