"""Tenant-scoped AI cache, usage ledger, conversational memory and embeddings."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from app.database.db import Base


class AIResponseCache(Base):
    __tablename__ = "ai_response_cache"
    key = Column(String(64), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    model = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class AICall(Base):
    __tablename__ = "ai_calls"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    agent = Column(String(64), nullable=False)
    model = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer, nullable=False, default=0)
    error_code = Column(String(128), nullable=True)


class ChatMemory(Base):
    __tablename__ = "chat_memory"
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    through_message_id = Column(Integer, nullable=False)


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (UniqueConstraint("chunk_id", "model", name="uq_chunk_embeddings_chunk_model"),)
    id = Column(Integer, primary_key=True)
    chunk_id = Column(Integer, ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False, index=True)
    model = Column(String(255), nullable=False)
    text_hash = Column(String(64), nullable=False)
    vector = Column(JSON, nullable=False)


class CanvasWorkspace(Base):
    __tablename__ = "canvas_workspaces"
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    revision = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
