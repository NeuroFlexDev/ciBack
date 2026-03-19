from pydantic import BaseModel


class SearchResultResponse(BaseModel):
    text: str
    type: str
    score: float
    course_id: int | None = None
    lesson_id: int | None = None
    source_name: str | None = None
    content_type: str | None = None
    chunk_index: int | None = None


class SearchResponse(BaseModel):
    results: list[SearchResultResponse]
