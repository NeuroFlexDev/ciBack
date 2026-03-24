from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    lesson_id: int
    type: str = Field(default="general", min_length=1)
    comment: str = ""
    rating: int = Field(default=5, ge=1, le=5)


class FeedbackResponse(BaseModel):
    id: int
    lesson_id: int
    author_id: int
    type: str
    comment: str | None
    rating: int | None
    created_at: datetime | None = None
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)
