from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CourseVersionResponse(BaseModel):
    id: int
    course_id: int
    name: str
    description: str | None
    level: int | None
    language: int | None
    created_at: datetime | None
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class ModuleVersionResponse(BaseModel):
    id: int
    course_version_id: int
    title: str
    created_at: datetime | None
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class LessonVersionResponse(BaseModel):
    id: int
    module_version_id: int
    title: str | None
    description: str | None
    created_at: datetime | None
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class RestoreCourseVersionResponse(BaseModel):
    ok: bool
    course_id: int
    course_version_id: int
