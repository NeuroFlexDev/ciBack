# app/models/user.py
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class User(Base, BaseModelMixin):
    __tablename__ = "users"

    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="student", server_default="student")

    courses = relationship("Course", back_populates="owner")
    feedback = relationship("Feedback", back_populates="author")
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
