from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.orm import relationship

from app.database.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    courses = relationship("Course", back_populates="owner")
    course_structures = relationship("CourseStructure", back_populates="owner")
    feedback = relationship("Feedback", back_populates="author")
    documents = relationship("Document", back_populates="owner")
    created_graphs = relationship("CourseGraph", back_populates="creator")
