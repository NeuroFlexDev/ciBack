from typing import TypedDict


class CourseAgentState(TypedDict, total=False):
    revision: int
    ingestion: dict
    competency_map: dict
    course_plan: dict
    writer: dict
    assessment: dict
    qa: dict
    repair_target: str
    legacy: dict
