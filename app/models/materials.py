from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, JSON, func, BigInteger
from app.database.db import Base


class OrganizationMaterialPolicy(Base):
    __tablename__ = "organization_material_policy"
    organization_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    disk_bytes_limit = Column(BigInteger, nullable=False, default=5 * 1024**3)
    image_monthly_limit = Column(Integer, nullable=False, default=50)
    chat_monthly_limit = Column(Integer, nullable=False, default=1000)


class ScopedAIUsage(Base):
    __tablename__ = "scoped_ai_usage"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
    operation = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False)
    model = Column(String(255), nullable=True)
    provider = Column(String(40), nullable=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class CourseImage(Base):
    __tablename__ = "course_images"
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="uq_course_images_request"),)
    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(64), nullable=True)
    prompt = Column(Text, nullable=True)
    status = Column(String(24), nullable=False)
    storage_key = Column(String(512), nullable=True)
    mime_type = Column(String(32), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    provider = Column(String(40), nullable=True)
    model = Column(String(255), nullable=True)
    error_code = Column(String(64), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_comment = Column(String(2000), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class LearnerAssistantMessage(Base):
    __tablename__ = "learner_assistant_messages"
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="uq_learner_assistant_request"),)
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    assignment_id = Column(Integer, nullable=False, index=True)
    course_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=False)
    lesson_id = Column(Integer, nullable=False)
    request_id = Column(String(64), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    citations = Column(JSON, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="pending")
    model = Column(String(255), nullable=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
