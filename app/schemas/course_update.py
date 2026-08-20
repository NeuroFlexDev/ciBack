from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CourseUpdateProposalOut(BaseModel):
    id: int
    course_id: int
    document_id: int
    base_graph_id: int | None
    detected_by_run_id: int | None
    source_versions: list[dict[str, Any]]
    source_hashes: list[str]
    affected_node_ids: list[str]
    proposed_diff: dict[str, Any]
    summary: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CourseUpdateProposalList(BaseModel):
    items: list[CourseUpdateProposalOut]
    total: int
    limit: int
    offset: int
