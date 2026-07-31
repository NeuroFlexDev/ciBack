from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain_enums import DocumentStatus as InternalDocumentStatus


class DocumentCreate(BaseModel):
    storage_key: str = Field(min_length=1, max_length=1024)
    owner_id: int = Field(gt=0)
    course_id: int = Field(gt=0)
    version: int = Field(default=1, gt=0)
    status: InternalDocumentStatus = InternalDocumentStatus.UPLOADED
    content_hash: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=64)
    original_filename: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)


class DocumentUpdate(BaseModel):
    status: InternalDocumentStatus | None = None
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


class DocumentStatus(str, Enum):
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


INTERNAL_TO_PUBLIC_STATUS = {
    InternalDocumentStatus.UPLOADED.value: DocumentStatus.PROCESSING,
    InternalDocumentStatus.QUEUED.value: DocumentStatus.PROCESSING,
    InternalDocumentStatus.PROCESSING.value: DocumentStatus.PROCESSING,
    InternalDocumentStatus.INDEXED.value: DocumentStatus.READY,
    InternalDocumentStatus.FAILED.value: DocumentStatus.ERROR,
}

PUBLIC_TO_INTERNAL_STATUSES = {
    DocumentStatus.PROCESSING: (
        InternalDocumentStatus.UPLOADED.value,
        InternalDocumentStatus.QUEUED.value,
        InternalDocumentStatus.PROCESSING.value,
    ),
    DocumentStatus.READY: (InternalDocumentStatus.INDEXED.value,),
    DocumentStatus.ERROR: (InternalDocumentStatus.FAILED.value,),
}


class DocumentListItem(BaseModel):
    id: int
    course_id: int
    version: int
    status: DocumentStatus
    source_type: str
    original_filename: str
    content_type: str
    size_bytes: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(DocumentListItem):
    pass


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int


def document_list_item(document) -> DocumentListItem:
    public_status = INTERNAL_TO_PUBLIC_STATUS.get(document.status)
    if public_status is None:
        raise ValueError(f"Document status is not public: {document.status}")
    return DocumentListItem(
        id=document.id,
        course_id=document.course_id,
        original_filename=document.original_filename,
        content_type=document.mime_type,
        size_bytes=document.size_bytes,
        source_type=document.source_type,
        version=document.version,
        status=public_status,
        error_message=document.processing_error,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )
