from datetime import datetime

from pydantic import BaseModel


class ReviewCourse(BaseModel):
    id: int
    title: str
    description: str | None
    difficulty: str | None
    language: str | None
    status: str
    updated_at: datetime


class ReviewMetrics(BaseModel):
    module_count: int
    lesson_count: int
    test_question_count: int
    estimated_duration_minutes: int


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


class ReviewModule(BaseModel):
    id: int
    course_id: int
    title: str
    position: int
    updated_at: datetime
    revision: int
    lessons: list[ReviewLesson]
    tests: list[ReviewTest]


class CourseReviewStructure(BaseModel):
    course: ReviewCourse
    metrics: ReviewMetrics
    modules: list[ReviewModule]
    final_tests: list[ReviewTest]


class ModuleDetail(ReviewModule):
    pass
