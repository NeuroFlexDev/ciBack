from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.domain_enums import GenerationRunStatus, GenerationRunType


class GenerationRunOut(BaseModel):
    id: int
    run_type: GenerationRunType
    course_id: int | None
    document_id: int | None
    status: GenerationRunStatus
    model: str | None
    input_docs: list[dict[str, Any]]
    output: dict[str, Any] | None
    cost_usd: Decimal | None
    latency_ms: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
