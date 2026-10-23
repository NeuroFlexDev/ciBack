"""Organization access, one-time invitations and administrative audit trail."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, func
from app.database.db import Base


class Organization(Base):
    __tablename__ = 'organizations'
    __table_args__ = (
        CheckConstraint("kind IN ('business','education','government','individual')", name='ck_organizations_kind'),
        CheckConstraint('seat_limit > 0 AND course_limit > 0 AND source_limit > 0 AND generation_limit >= 0', name='ck_organizations_limits'),
    )
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    kind = Column(String(16), nullable=False, default='business', server_default='business')
    is_active = Column(Boolean, nullable=False, default=True, server_default='true')
    seat_limit = Column(Integer, nullable=False, default=10, server_default='10')
    course_limit = Column(Integer, nullable=False, default=50, server_default='50')
    source_limit = Column(Integer, nullable=False, default=20, server_default='20')
    generation_limit = Column(Integer, nullable=False, default=100, server_default='100')
    ai_unlimited = Column(Boolean, nullable=False, default=False, server_default='false')
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class Invitation(Base):
    __tablename__ = 'invitations'
    __table_args__ = (CheckConstraint("role IN ('owner','admin','author','reviewer','manager','member','candidate')", name='ck_invitations_role'),)
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    email = Column(String(255), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False, index=True)
    role = Column(String(16), nullable=False, default='member')
    position_id = Column(Integer, ForeignKey("business_positions.id"), nullable=True, index=True)
    manager_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    invited_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class AdminEvent(Base):
    __tablename__ = 'admin_events'
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    action = Column(String(64), nullable=False)
    target_type = Column(String(32), nullable=False)
    target_id = Column(String(64), nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
