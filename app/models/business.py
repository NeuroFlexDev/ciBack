"""B2B job profiles; courses and employee membership retain tenant ownership."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from app.database.db import Base


class BusinessPosition(Base):
    __tablename__ = "business_positions"
    __table_args__ = (UniqueConstraint("organization_id", "name_key", name="uq_business_position_name"),)
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    name_key = Column(String(600), nullable=False)
    description = Column(Text, nullable=False, default="")
    responsibilities = Column(Text, nullable=False, default="")
    competencies = Column(Text, nullable=False, default="")
    is_archived = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
