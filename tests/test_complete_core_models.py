from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.approval import Approval
from app.models.assessment_rubric import AssessmentRubric
from app.models.competency import Competency
from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.domain_enums import ApprovalDecision, CourseGraphStatus
from app.models.learning_event import LearningEvent
from app.models.learning_objective import LearningObjective
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.task import Task
from app.schemas.core_domain import (
    ApprovalCreate,
    AssessmentRubricCreate,
    LearningEventCreate,
    LearningObjectiveCreate,
)


def _course_hierarchy(db_session, auth_user):
    course = Course(name="Core models", owner_id=auth_user.id)
    module = Module(title="Module", course=course)
    lesson = Lesson(title="Lesson", module=module)
    task = Task(name="Task", module=module)
    graph = CourseGraph(
        course=course,
        version=1,
        nodes=[{"id": "lesson-1"}],
        edges=[],
        created_by=auth_user.id,
        status=CourseGraphStatus.DRAFT.value,
    )
    db_session.add_all([course, module, lesson, task, graph])
    db_session.flush()
    return course, module, lesson, task, graph


def test_complete_core_models_round_trip(db_session, auth_user):
    course, module, _, task, graph = _course_hierarchy(db_session, auth_user)
    competency = Competency(
        course=course,
        title="Explain architecture",
        level="intermediate",
        job_role="Backend developer",
    )
    objective = LearningObjective(
        course=course,
        module=module,
        bloom_level="understand",
        measurable_verb="explain",
        text="Explain the service architecture",
        linked_node_ids=["lesson-1"],
    )
    rubric = AssessmentRubric(
        course=course,
        task=task,
        competency=competency,
        criteria=[{"key": "accuracy", "title": "Accuracy"}],
        levels=[{"key": "good", "title": "Good", "min_score": 80}],
    )
    approval = Approval(
        course_graph=graph,
        reviewer=auth_user,
        diff={"added": ["lesson-1"]},
        decision=ApprovalDecision.APPROVED.value,
        comment="Ready",
    )
    event = LearningEvent(
        user=auth_user,
        course=course,
        actor={"id": str(auth_user.id)},
        verb={"id": "completed"},
        object={"id": "lesson-1"},
        result={"success": True},
        context={"source": "web"},
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add_all([competency, objective, rubric, approval, event])
    db_session.commit()

    db_session.expire_all()
    stored_course = db_session.get(Course, course.id)

    assert stored_course.competencies[0].job_role == "Backend developer"
    assert stored_course.learning_objectives[0].linked_node_ids == ["lesson-1"]
    assert stored_course.assessment_rubrics[0].competency.title == "Explain architecture"
    assert graph.approvals[0].decision == ApprovalDecision.APPROVED.value
    assert stored_course.learning_events[0].result == {"success": True}


def test_learning_objective_rejects_two_detail_scopes(db_session, auth_user):
    course, module, lesson, _, _ = _course_hierarchy(db_session, auth_user)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                LearningObjective(
                    course_id=course.id,
                    module_id=module.id,
                    lesson_id=lesson.id,
                    bloom_level="apply",
                    measurable_verb="build",
                    text="Build a service",
                    linked_node_ids=[],
                )
            )
            db_session.flush()


def test_core_domain_schemas_validate_structured_payloads():
    objective = LearningObjectiveCreate(
        course_id=1,
        bloom_level="understand",
        measurable_verb="explain",
        text="Explain a concept",
        linked_node_ids=["node-1"],
    )
    approval = ApprovalCreate(course_graph_id=1, reviewer_id=2)
    event = LearningEventCreate(
        user_id=2,
        actor={"id": "2"},
        verb={"id": "completed"},
        object={"id": "lesson-1"},
        occurred_at=datetime.now(timezone.utc),
    )

    assert objective.linked_node_ids == ["node-1"]
    assert approval.decision is ApprovalDecision.PENDING
    assert event.result == {}

    with pytest.raises(ValidationError):
        LearningObjectiveCreate(
            course_id=1,
            module_id=1,
            lesson_id=2,
            bloom_level="apply",
            measurable_verb="build",
            text="Build",
        )

    with pytest.raises(ValidationError):
        AssessmentRubricCreate(
            course_id=1,
            criteria=[
                {"key": "same", "title": "First"},
                {"key": "same", "title": "Second"},
            ],
            levels=[
                {
                    "key": "bad-range",
                    "title": "Bad",
                    "min_score": 10,
                    "max_score": 5,
                }
            ],
        )
