from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from app.database.db import Base
from app.models.base import BaseModelMixin
from app.models.domain_enums import CourseStatus

class Course(Base, BaseModelMixin):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'configured', 'generating', 'ready', 'generation_failed')",
            name="ck_courses_status",
        ),
        CheckConstraint(
            "status = 'draft' OR name IS NOT NULL",
            name="ck_courses_non_draft_name",
        ),
        Index("ix_courses_owner_status", "owner_id", "status"),
    )

    name = Column(String, nullable=True)
    description = Column(String, nullable=True)
    level = Column(String, nullable=True)
    language = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    status = Column(
        String(32), nullable=False, default=CourseStatus.READY.value, index=True
    )
    current_graph_id = Column(
        Integer,
        ForeignKey(
            "course_graphs.id",
            name="fk_courses_current_graph_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )

    # Связь с модулями
    owner = relationship("User", back_populates="courses")
    modules = relationship("Module", back_populates="course", cascade="all, delete-orphan")
    course_modules = relationship(
        "CourseModule", back_populates="course", cascade="all, delete-orphan"
    )
    documents = relationship(
        "Document", back_populates="course", cascade="all, delete-orphan"
    )
    graphs = relationship(
        "CourseGraph",
        back_populates="course",
        foreign_keys="CourseGraph.course_id",
        cascade="all, delete-orphan",
    )
    current_graph = relationship(
        "CourseGraph", foreign_keys=[current_graph_id], post_update=True
    )
    chats = relationship("Chat", back_populates="course")
    generation_runs = relationship(
        "GenerationRun", back_populates="course", cascade="all, delete-orphan"
    )
    competencies = relationship(
        "Competency", back_populates="course", cascade="all, delete-orphan"
    )
    learning_objectives = relationship(
        "LearningObjective", back_populates="course", cascade="all, delete-orphan"
    )
    assessment_rubrics = relationship(
        "AssessmentRubric", back_populates="course", cascade="all, delete-orphan"
    )
    learning_events = relationship("LearningEvent", back_populates="course")
