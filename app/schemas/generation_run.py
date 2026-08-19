from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from app.models.domain_enums import GenerationRunStatus, GenerationRunType
from app.schemas.course_generation_settings import CourseGenerationSettingsUpdate


class GenerationRunCreate(BaseModel):
    settings: CourseGenerationSettingsUpdate
    document_ids: list[int] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class GenerationRunAccepted(BaseModel):
    run_id: int
    course_id: int
    status: GenerationRunStatus
    status_url: str


class GenerationStageOut(BaseModel):
    code: Literal[
        "ingestion",
        "competency_mapping",
        "course_architecture",
        "lesson_writing",
        "assessment_generation",
        "quality_assurance",
        "materialization",
    ]
    title: str
    status: Literal["pending", "running", "completed"]


class GenerationStatusError(BaseModel):
    code: str
    message: str
    retryable: bool


class GenerationRunOut(BaseModel):
    id: int
    run_type: GenerationRunType
    course_id: int | None
    document_id: int | None
    status: GenerationRunStatus
    model: str | None
    input_docs: list[dict[str, Any]]
    settings_snapshot: dict[str, Any]
    input_documents_snapshot: list[dict[str, Any]]
    output: dict[str, Any] | None
    cost_usd: Decimal | None
    latency_ms: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenerationRunStatusOut(GenerationRunOut):
    run_id: int
    current_stage: str
    progress_percent: int = Field(ge=0, le=100)
    stages: list[GenerationStageOut]
    status_error: GenerationStatusError | None
    retryable: bool
    attempt: int
    retry_of_run_id: int | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
