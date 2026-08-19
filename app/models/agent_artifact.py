from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class AgentArtifact(Base, BaseModelMixin):
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        CheckConstraint(
            "schema_version > 0", name="ck_agent_artifacts_schema_version_positive"
        ),
        CheckConstraint(
            "sequence >= 0", name="ck_agent_artifacts_sequence_nonnegative"
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_agent_artifacts_latency_nonnegative",
        ),
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_agent_artifacts_status",
        ),
        UniqueConstraint(
            "run_id",
            "agent",
            "sequence",
            name="uq_agent_artifacts_run_agent_sequence",
        ),
        Index("ix_agent_artifacts_run_status", "run_id", "status"),
        Index(
            "ix_agent_artifacts_course_artifact", "course_id", "artifact"
        ),
    )

    run_id = Column(
        Integer,
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    agent = Column(String(64), nullable=False)
    artifact = Column(String(64), nullable=False)
    schema_version = Column(
        Integer, nullable=False, default=1, server_default="1"
    )
    sequence = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(
        String(32), nullable=False, default="completed", server_default="completed"
    )
    payload = Column(JSON_PAYLOAD, nullable=False, default=dict)
    input_fingerprint = Column(String(128), nullable=True)
    model = Column(String(255), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)

    run = relationship("GenerationRun")
    course = relationship("Course")
