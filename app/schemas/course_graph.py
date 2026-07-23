from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain_enums import CourseGraphStatus


class CourseGraphCreate(BaseModel):
    course_id: int = Field(gt=0)
    version: int = Field(gt=0)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    created_by: int = Field(gt=0)
    status: CourseGraphStatus = CourseGraphStatus.DRAFT


class CourseGraphUpdate(BaseModel):
    status: CourseGraphStatus


class CourseGraphOut(CourseGraphCreate):
    id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)
