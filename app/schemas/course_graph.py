from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.domain_enums import CourseGraphStatus


class CourseGraphCreate(BaseModel):
    course_id: int = Field(gt=0)
    version: int = Field(gt=0)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    created_by: int = Field(gt=0)
    status: CourseGraphStatus = CourseGraphStatus.DRAFT


class CourseGraphUpdate(BaseModel):
    status: CourseGraphStatus


class CourseGraphOut(CourseGraphCreate):
    id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)


class CanvasNode(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["module", "lesson", "test", "task"]

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_namespaced_id(self):
        prefix, separator, raw_id = self.id.partition(":")
        if separator != ":" or prefix != self.type or not raw_id.isdigit() or int(raw_id) <= 0:
            raise ValueError("canvas id must match '<type>:<positive database id>'")
        return self


class CanvasEdge(BaseModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)

    model_config = ConfigDict(extra="allow")


class CanvasPut(BaseModel):
    version: int = Field(ge=0)
    nodes: list[CanvasNode] = Field(default_factory=list)
    edges: list[CanvasEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self):
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be unique")
        known_nodes = set(node_ids)
        for edge in self.edges:
            if edge.source not in known_nodes or edge.target not in known_nodes:
                raise ValueError("edge source and target must reference existing nodes")
        return self


class CanvasOut(BaseModel):
    course_id: int
    graph_id: int | None
    version: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    status: CourseGraphStatus | None
    created_by: int | None
    created_at: datetime | None
    updated_at: datetime | None


class CanvasVersionSummary(BaseModel):
    graph_id: int
    version: int
    status: CourseGraphStatus
    is_current: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class CanvasVersionListOut(BaseModel):
    items: list[CanvasVersionSummary]
    total: int
    limit: int
    offset: int


class CanvasVersionOut(CanvasVersionSummary):
    course_id: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
