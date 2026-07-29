from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin
from app.models.domain_enums import ApprovalDecision


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class Approval(Base, BaseModelMixin):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'changes_requested')",
            name="ck_approvals_decision",
        ),
        Index("ix_approvals_graph_decision", "course_graph_id", "decision"),
    )

    course_graph_id = Column(
        Integer,
        ForeignKey("course_graphs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    diff = Column(JSON_PAYLOAD, nullable=False, default=dict)
    decision = Column(
        String(32), nullable=False, default=ApprovalDecision.PENDING.value
    )
    comment = Column(Text, nullable=True)

    course_graph = relationship("CourseGraph", back_populates="approvals")
    reviewer = relationship("User", back_populates="approvals")
