from typing import Any

from pydantic import BaseModel, Field, model_validator


class GeneratedGraphNode(BaseModel):
    id: str = Field(min_length=1)

    model_config = {"extra": "allow"}


class GeneratedGraphEdge(BaseModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)

    model_config = {"extra": "allow"}


class GeneratedGraphPayload(BaseModel):
    nodes: list[GeneratedGraphNode]
    edges: list[GeneratedGraphEdge]

    @model_validator(mode="after")
    def validate_references(self):
        node_ids = [node.id for node in self.nodes]
        if not node_ids:
            raise ValueError("generated graph must contain nodes")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("generated graph node ids must be unique")
        known = set(node_ids)
        if any(edge.source not in known or edge.target not in known for edge in self.edges):
            raise ValueError("generated graph edge references an unknown node")
        return self

    def json_payload(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            [node.model_dump() for node in self.nodes],
            [edge.model_dump() for edge in self.edges],
        )
