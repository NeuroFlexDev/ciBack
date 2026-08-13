from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CoursePublishResponse(BaseModel):
    id: int
    publication_status: Literal["published"]
    published_at: datetime
    revision: int = Field(gt=0)


class CourseListItem(BaseModel):
    id: int
    title: str
    description: str | None
    level: int
    language: int
    publication_status: Literal["draft", "published"]
    module_count: int = Field(ge=0)
    lesson_count: int = Field(ge=0)
    updated_at: datetime
    published_at: datetime | None
