"""Personal second factors and single-use authentication challenges."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, BigInteger, JSON, String, Text
from app.database.db import Base


class UserMFA(Base):
    __tablename__ = 'user_mfa'
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    secret_encrypted = Column(Text, nullable=False, default='')
    last_totp_step = Column(BigInteger, nullable=False, default=-1)
    pending_secret_encrypted = Column(Text, nullable=False, default='')
    enrollment_token_hash = Column(String(64), nullable=True)
    enrollment_expires_at = Column(DateTime, nullable=True)
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    enabled_at = Column(DateTime, nullable=True)


class MFARecoveryCode(Base):
    __tablename__ = 'mfa_recovery_codes'
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    code_hash = Column(String(64), primary_key=True)
    consumed_at = Column(DateTime, nullable=True)


class MFAChallenge(Base):
    __tablename__ = 'mfa_challenges'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    auth_version = Column(Integer, nullable=False)
    primary_method = Column(String(16), nullable=False)
    context = Column(JSON, nullable=False, default=dict)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
