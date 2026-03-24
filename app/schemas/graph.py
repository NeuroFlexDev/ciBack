from pydantic import BaseModel


class GraphNodeResponse(BaseModel):
    id: str
    label: str
    type: str


class GraphEdgeResponse(BaseModel):
    source: str
    target: str
    relation: str


class CourseGraphResponse(BaseModel):
    course_id: int
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
