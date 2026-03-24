import networkx as nx
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.module import Module
from app.schemas.graph import CourseGraphResponse, GraphEdgeResponse, GraphNodeResponse


def build_course_graph(course_id: int, db: Session) -> CourseGraphResponse:
    course = db.query(Course).filter(Course.id == course_id, Course.is_deleted == False).first()
    if not course:
        raise ValueError("Course not found")

    graph = nx.Graph()
    graph.add_node(f"course_{course.id}", label=course.name, type="course")

    modules = db.query(Module).filter(Module.course_id == course_id).all()
    for module in modules:
        if module.is_deleted:
            continue
        graph.add_node(f"module_{module.id}", label=module.title, type="module")
        graph.add_edge(f"course_{course.id}", f"module_{module.id}", relation="contains")
        for lesson in module.lessons:
            if lesson.is_deleted:
                continue
            graph.add_node(f"lesson_{lesson.id}", label=lesson.title, type="lesson")
            graph.add_edge(f"module_{module.id}", f"lesson_{lesson.id}", relation="contains")

    data = nx.readwrite.json_graph.node_link_data(graph, edges="links")
    return CourseGraphResponse(
        course_id=course_id,
        nodes=[
            GraphNodeResponse(
                id=str(node["id"]),
                label=str(node.get("label", "")),
                type=str(node.get("type", "")),
            )
            for node in data.get("nodes", [])
        ],
        edges=[
            GraphEdgeResponse(
                source=str(link.get("source")),
                target=str(link.get("target")),
                relation=str(link.get("relation", "contains")),
            )
            for link in data.get("links", [])
        ],
    )
