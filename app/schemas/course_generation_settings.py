from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain_enums import CourseDifficulty, CourseLanguage, CourseStatus


class CourseGenerationSettingsUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    target_audience: str | None = Field(default=None, max_length=1000)
    difficulty: CourseDifficulty
    language: CourseLanguage
    lesson_count: int = Field(ge=1, le=100)
    module_tests_enabled: bool
    final_test_enabled: bool

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "goal", mode="before")
    @classmethod
    def strip_required_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("target_audience", mode="before")
    @classmethod
    def strip_optional_text(cls, value):
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


class CourseGenerationSettingsResponse(BaseModel):
    course_id: int
    title: str
    goal: str
    target_audience: str | None
    difficulty: CourseDifficulty
    language: CourseLanguage
    lesson_count: int
    module_tests_enabled: bool
    final_test_enabled: bool
    course_status: CourseStatus
    created_at: datetime
    updated_at: datetime
