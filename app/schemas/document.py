from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain_enums import DocumentStatus


class DocumentCreate(BaseModel):
    storage_key: str = Field(min_length=1, max_length=1024)
    owner_id: int = Field(gt=0)
    course_id: int = Field(gt=0)
    version: int = Field(default=1, gt=0)
    status: DocumentStatus = DocumentStatus.UPLOADED
    content_hash: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=64)
    original_filename: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)


class DocumentUpdate(BaseModel):
    status: DocumentStatus | None = None
    processing_error: str | None = None


class DocumentOut(DocumentCreate):
    id: int
    processing_error: str | None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class DocumentChunkCreate(BaseModel):
    document_id: int = Field(gt=0)
    document_version: int = Field(gt=0)
    text: str = Field(min_length=1)
    embedding_id: str | None = Field(default=None, max_length=255)
    page: int | None = Field(default=None, gt=0)
    section: str | None = Field(default=None, max_length=512)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    chunk_index: int = Field(ge=0)


class DocumentChunkUpdate(BaseModel):
    embedding_id: str | None = Field(default=None, max_length=255)
    metadata_json: dict[str, Any] | None = None


class DocumentChunkOut(DocumentChunkCreate):
    id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class DocumentPublicOut(BaseModel):
    id: int
    course_id: int
    version: int
    status: DocumentStatus
    source_type: str
    original_filename: str
    mime_type: str
    size_bytes: int
    processing_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListOut(BaseModel):
    items: list[DocumentPublicOut]
    total: int
    limit: int
    offset: int
