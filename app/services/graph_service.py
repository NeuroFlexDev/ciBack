# app/services/graph_service.py

import networkx as nx
from sqlalchemy.orm import Session

from app.models.module import Module


def build_course_graph(course_id: int, db: Session):
    G = nx.Graph()

    modules = db.query(Module).filter(Module.course_id == course_id).all()
    for module in modules:
        G.add_node(f"module_{module.id}", label=module.title, type="module")
        for lesson in module.lessons:
            G.add_node(f"lesson_{lesson.id}", label=lesson.title, type="lesson")
            G.add_edge(f"module_{module.id}", f"lesson_{lesson.id}", relation="contains")

    return nx.readwrite.json_graph.node_link_data(G)
