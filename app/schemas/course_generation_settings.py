from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    approved_plan: list[str] = Field(default_factory=list, max_length=100)
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def approved_plan_matches_count(self):
        self.approved_plan = [title.strip() for title in self.approved_plan]
        if any(not title or len(title) > 200 for title in self.approved_plan):
            raise ValueError("Each approved lesson title must contain 1–200 characters")
        if self.approved_plan and len(self.approved_plan) != self.lesson_count:
            raise ValueError("Approved plan must contain exactly lesson_count lessons")
        return self

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
    revision: int = 1
    title: str
    goal: str
    target_audience: str | None
    difficulty: CourseDifficulty
    language: CourseLanguage
    lesson_count: int
    module_tests_enabled: bool
    final_test_enabled: bool
    approved_plan: list[str] = Field(default_factory=list)
    course_status: CourseStatus
    created_at: datetime
    updated_at: datetime
