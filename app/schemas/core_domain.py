from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.domain_enums import ApprovalDecision


class DomainOut(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class CompetencyCreate(BaseModel):
    course_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    level: str | None = Field(default=None, max_length=64)
    job_role: str | None = Field(default=None, max_length=255)


class CompetencyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    level: str | None = Field(default=None, max_length=64)
    job_role: str | None = Field(default=None, max_length=255)


class CompetencyOut(CompetencyCreate, DomainOut):
    pass


class LearningObjectiveCreate(BaseModel):
    course_id: int = Field(gt=0)
    module_id: int | None = Field(default=None, gt=0)
    lesson_id: int | None = Field(default=None, gt=0)
    bloom_level: str = Field(min_length=1, max_length=64)
    measurable_verb: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    linked_node_ids: list[str] = Field(default_factory=list)

    @field_validator("linked_node_ids")
    @classmethod
    def validate_linked_node_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("linked node ids must not be blank")
        if len(values) != len(set(values)):
            raise ValueError("linked node ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_detail_scope(self):
        if self.module_id is not None and self.lesson_id is not None:
            raise ValueError("module_id and lesson_id cannot both be set")
        return self


class LearningObjectiveUpdate(BaseModel):
    module_id: int | None = Field(default=None, gt=0)
    lesson_id: int | None = Field(default=None, gt=0)
    bloom_level: str | None = Field(default=None, min_length=1, max_length=64)
    measurable_verb: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1)
    linked_node_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_detail_scope(self):
        fields_set = self.model_fields_set
        if (
            "module_id" in fields_set
            and "lesson_id" in fields_set
            and self.module_id is not None
            and self.lesson_id is not None
        ):
            raise ValueError("module_id and lesson_id cannot both be set")
        if self.linked_node_ids is not None:
            if any(not value.strip() for value in self.linked_node_ids):
                raise ValueError("linked node ids must not be blank")
            if len(self.linked_node_ids) != len(set(self.linked_node_ids)):
                raise ValueError("linked node ids must be unique")
        return self


class LearningObjectiveOut(LearningObjectiveCreate, DomainOut):
    pass


class RubricCriterion(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    weight: float | None = Field(default=None, ge=0)


class RubricLevel(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    min_score: float | None = None
    max_score: float | None = None

    @model_validator(mode="after")
    def validate_score_range(self):
        if (
            self.min_score is not None
            and self.max_score is not None
            and self.min_score > self.max_score
        ):
            raise ValueError("min_score must be less than or equal to max_score")
        return self


class AssessmentRubricCreate(BaseModel):
    course_id: int = Field(gt=0)
    task_id: int | None = Field(default=None, gt=0)
    competency_id: int | None = Field(default=None, gt=0)
    criteria: list[RubricCriterion] = Field(default_factory=list)
    levels: list[RubricLevel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_keys(self):
        for label, values in (("criterion", self.criteria), ("level", self.levels)):
            keys = [value.key for value in values]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{label} keys must be unique")
        return self


class AssessmentRubricUpdate(BaseModel):
    task_id: int | None = Field(default=None, gt=0)
    competency_id: int | None = Field(default=None, gt=0)
    criteria: list[RubricCriterion] | None = None
    levels: list[RubricLevel] | None = None

    @model_validator(mode="after")
    def validate_unique_keys(self):
        for label, values in (("criterion", self.criteria), ("level", self.levels)):
            if values is None:
                continue
            keys = [value.key for value in values]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{label} keys must be unique")
        return self


class AssessmentRubricOut(AssessmentRubricCreate, DomainOut):
    pass


class ApprovalCreate(BaseModel):
    course_graph_id: int = Field(gt=0)
    reviewer_id: int = Field(gt=0)
    diff: dict[str, Any] = Field(default_factory=dict)
    decision: ApprovalDecision = ApprovalDecision.PENDING
    comment: str | None = None


class ApprovalUpdate(BaseModel):
    diff: dict[str, Any] | None = None
    decision: ApprovalDecision | None = None
    comment: str | None = None


class ApprovalOut(ApprovalCreate, DomainOut):
    pass


class LearningEventCreate(BaseModel):
    user_id: int = Field(gt=0)
    course_id: int | None = Field(default=None, gt=0)
    actor: dict[str, Any]
    verb: dict[str, Any]
    object: dict[str, Any]
    result: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class LearningEventUpdate(BaseModel):
    result: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    occurred_at: datetime | None = None


class LearningEventOut(LearningEventCreate, DomainOut):
    pass
