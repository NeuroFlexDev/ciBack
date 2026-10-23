from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from app.database.db import Base


class PasswordReset(Base):
    __tablename__ = "password_resets"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)


class EmailBinding(Base):
    __tablename__ = 'email_bindings'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    auth_version = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
