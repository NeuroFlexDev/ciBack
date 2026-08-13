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
    DateTime,
    Boolean,
    text,
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
            "status IN ('queued', 'running', 'succeeded', 'completed', 'failed')",
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
        CheckConstraint("progress_percent BETWEEN 0 AND 100", name="ck_generation_runs_progress"),
        CheckConstraint("attempt > 0", name="ck_generation_runs_attempt_positive"),
        Index("ix_generation_runs_owner_status", "owner_id", "status"),
        Index(
            "uq_generation_runs_active_graph_course",
            "course_id",
            unique=True,
            postgresql_where=text("run_type = 'graph_generation' AND status IN ('queued', 'running') AND is_deleted = false"),
            sqlite_where=text("run_type = 'graph_generation' AND status IN ('queued', 'running') AND is_deleted = 0"),
        ),
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
    input_documents_snapshot = Column(JSON_PAYLOAD, nullable=False, default=list)
    input_fingerprint = Column(String(128), nullable=True)
    output = Column(JSON_PAYLOAD, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    current_stage = Column(String(64), nullable=False, default="queued")
    progress_percent = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    retryable = Column(Boolean, nullable=False, default=False)
    attempt = Column(Integer, nullable=False, default=1)
    queued_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    retry_of_run_id = Column(
        Integer,
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    owner = relationship("User", back_populates="generation_runs")
    course = relationship("Course", back_populates="generation_runs")
    document = relationship("Document", back_populates="generation_runs")
    retry_of_run = relationship("GenerationRun", remote_side="GenerationRun.id")
