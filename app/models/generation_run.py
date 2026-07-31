from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin
from app.models.domain_enums import GenerationRunStatus


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class GenerationRun(Base, BaseModelMixin):
    __tablename__ = "generation_runs"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('document_index', 'graph_generation')",
            name="ck_generation_runs_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_generation_runs_status",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_generation_runs_latency_nonnegative",
        ),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_generation_runs_cost_nonnegative",
        ),
        Index("ix_generation_runs_owner_status", "owner_id", "status"),
        Index(
            "ix_generation_runs_fingerprint",
            "owner_id",
            "course_id",
            "run_type",
            "input_fingerprint",
        ),
    )

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_type = Column(String(32), nullable=False)
    status = Column(
        String(32), nullable=False, default=GenerationRunStatus.QUEUED.value
    )
    prompt = Column(Text, nullable=True)
    model = Column(String(255), nullable=True)
    input_docs = Column(JSON_PAYLOAD, nullable=False, default=list)
    settings_snapshot = Column(JSON_PAYLOAD, nullable=False, default=dict)
    input_fingerprint = Column(String(128), nullable=True)
    output = Column(JSON_PAYLOAD, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)

    owner = relationship("User", back_populates="generation_runs")
    course = relationship("Course", back_populates="generation_runs")
    document = relationship("Document", back_populates="generation_runs")
