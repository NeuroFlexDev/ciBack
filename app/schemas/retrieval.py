from pydantic import BaseModel, Field


class RetrievalCitation(BaseModel):
    chunk_id: int
    document_id: int
    document_version: int
    source_document: str
    source_type: str
    page: int | None
    section: str | None
    text: str
    score: float = Field(ge=0.0, le=1.0)


class RetrievalResponse(BaseModel):
    query: str
    course_id: int
    citations: list[RetrievalCitation]
