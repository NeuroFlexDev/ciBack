from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AgentArtifactOut(BaseModel):
    id: int
    run_id: int
    course_id: int
    agent: str
    artifact: str
    schema_version: int
    sequence: int
    status: str
    payload: dict[str, Any]
    input_fingerprint: str | None
    model: str | None
    latency_ms: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
