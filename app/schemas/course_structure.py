from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_content_types(value: List[str] | str | None) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    else:
        candidates = value

    normalized = [str(item).strip() for item in candidates]
    cleaned = [item for item in normalized if item]
    if len(cleaned) != len(normalized):
        raise ValueError("content_types must not contain empty values")
    return cleaned


class CourseStructureCreate(BaseModel):
    sections: int = Field(..., gt=0)
    tests_per_section: int = Field(..., ge=0)
    lessons_per_section: int = Field(..., gt=0)
    questions_per_test: int = Field(..., ge=0)
    final_test: bool
    content_types: List[str] = Field(default_factory=list)

    @field_validator("content_types", mode="before")
    @classmethod
    def validate_content_types(cls, value: List[str] | str | None) -> List[str]:
        return normalize_content_types(value)


class CourseStructureUpdate(BaseModel):
    sections: int | None = Field(None, gt=0)
    tests_per_section: int | None = Field(None, ge=0)
    lessons_per_section: int | None = Field(None, gt=0)
    questions_per_test: int | None = Field(None, ge=0)
    final_test: bool | None = None
    content_types: List[str] | None = None

    @field_validator("content_types", mode="before")
    @classmethod
    def validate_content_types(cls, value: List[str] | str | None) -> List[str] | None:
        if value is None:
            return None
        return normalize_content_types(value)


class CourseStructureResponse(BaseModel):
    id: int
    sections: int
    tests_per_section: int
    lessons_per_section: int
    questions_per_test: int
    final_test: bool
    content_types: List[str]
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)
