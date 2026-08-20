from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReviewStatus = Literal["draft", "ready", "published"]


class ReviewMetrics(BaseModel):
    modules_count: int = Field(ge=0)
    lessons_count: int = Field(ge=0)
    tests_count: int = Field(ge=0)
    tasks_count: int = Field(ge=0)
    estimated_time_minutes: int = Field(ge=0)


class ModuleTimelineItem(BaseModel):
    module_id: int
    order: int = Field(gt=0)
    title: str
    description: str
    lessons_count: int = Field(ge=0)
    tests_count: int = Field(ge=0)
    tasks_count: int = Field(ge=0)
    estimated_time_minutes: int = Field(ge=0)
    status: ReviewStatus


class CourseReviewStructure(BaseModel):
    course_id: int
    title: str
    description: str
    status: ReviewStatus
    metrics: ReviewMetrics
    modules_timeline: list[ModuleTimelineItem]


class ReviewTest(BaseModel):
    id: int
    question: str
    answers: list[str]
    correct_answer: str
    position: int
    updated_at: datetime
    revision: int


class ReviewLesson(BaseModel):
    id: int
    module_id: int
    title: str
    description: str | None
    content: str
    position: int
    estimated_duration_minutes: int
    updated_at: datetime
    revision: int


class ReviewTask(BaseModel):
    id: int
    module_id: int
    name: str
    description: str
    updated_at: datetime


class ModuleDetail(BaseModel):
    id: int
    course_id: int
    title: str
    description: str
    status: ReviewStatus
    position: int
    updated_at: datetime
    revision: int
    lessons: list[ReviewLesson]
    tests: list[ReviewTest]
    tasks: list[ReviewTask]
