from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from app.database.db import Base


class LegalReceipt(Base):
    """Append-only application events; the accepted text survives catalog changes."""
    __tablename__ = "legal_receipts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    subject_email = Column(String(255), nullable=False)
    document_slug = Column(String(64), nullable=False)
    document_version = Column(String(32), nullable=False)
    document_sha256 = Column(String(64), nullable=False)
    document_snapshot = Column(JSON, nullable=False)
    action = Column(String(16), nullable=False)
    context = Column(String(96), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LegalRequest(Base):
    __tablename__ = "legal_requests"

    reference = Column(String(36), primary_key=True)
    purpose = Column(String(16), nullable=False)
    email = Column(String(255), nullable=False)
    name = Column(String(160), nullable=False, default="")
    message = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
