from sqlalchemy import Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.orm import relationship

from app.database.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String(160), nullable=False, default="", server_default="")
    auth_version = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    courses = relationship("Course", back_populates="owner")
    course_structures = relationship("CourseStructure", back_populates="owner")
    feedback = relationship("Feedback", back_populates="author")
    documents = relationship("Document", back_populates="owner")
    created_graphs = relationship("CourseGraph", back_populates="creator")
    chats = relationship("Chat", back_populates="owner")
    generation_runs = relationship("GenerationRun", back_populates="owner")
    approvals = relationship("Approval", back_populates="reviewer")
    learning_events = relationship("LearningEvent", back_populates="user")
