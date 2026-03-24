from pydantic import BaseModel, Field


class ImproveTheoryRequest(BaseModel):
    lesson_id: int
    goal: str = Field(..., min_length=1)


class ImproveTheoryResponse(BaseModel):
    lesson_id: int
    original: str
    improved: str
    used_external_context: bool
