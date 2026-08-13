from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class CourseVersion(Base, BaseModelMixin):
    __tablename__ = "course_versions"
    __table_args__ = (
        UniqueConstraint("course_id", "revision", name="uq_course_versions_course_revision"),
        CheckConstraint("revision > 0", name="ck_course_versions_revision_positive"),
        CheckConstraint("publication_status IN ('draft', 'published')", name="ck_course_versions_publication_status"),
    )

    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    publication_status = Column(String(16), nullable=False, default="published")
    published_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    level = Column(String)
    language = Column(String)
    created_at = Column(DateTime, default=func.now())

    modules = relationship("ModuleVersion", back_populates="course_version")
    tests = relationship("TestVersion", back_populates="course_version")
    course = relationship("Course", back_populates="versions")
