from __future__ import annotations

from collections import Counter, defaultdict
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.agentic_pipeline import (
    AgenticContract,
    AnswerOption,
    AssessmentRubric,
    CaseId,
    CompetencyId,
    LessonId,
    ModuleId,
    ObjectiveId,
    OptionId,
    PlanId,
    PracticeId,
    QuestionId,
    SourceRefId,
)


CanvasTestId = Annotated[
    str, Field(pattern=r"^test:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]


def _require_unique(label: str, values: list[str]) -> None:
    duplicates = sorted(
        value for value, count in Counter(values).items() if count > 1
    )
    if duplicates:
        raise ValueError(f"{label} must be unique; duplicates: {duplicates}")


def _require_known(label: str, values: list[str], known: set[str]) -> None:
    unknown = sorted(set(values) - known)
    if unknown:
        raise ValueError(f"{label} reference unknown ids: {unknown}")


def _require_exact(label: str, values: list[str], expected: set[str]) -> None:
    actual = set(values)
    if actual != expected or len(values) != len(actual):
        raise ValueError(
            f"{label} must match exactly; "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )


def _require_contiguous_positions(label: str, values: list) -> None:
    positions = [item.position for item in values]
    if sorted(positions) != list(range(len(values))):
        raise ValueError(f"{label} must be unique and contiguous from zero")


def _validate_dag(label: str, dependencies: dict[str, list[str]]) -> None:
    known = set(dependencies)
    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        marker = state.get(node_id, 0)
        if marker == 1:
            raise ValueError(f"{label} contains a cycle at {node_id}")
        if marker == 2:
            return
        state[node_id] = 1
        for prerequisite_id in dependencies[node_id]:
            if prerequisite_id not in known:
                raise ValueError(
                    f"{label} reference unknown prerequisite id: {prerequisite_id}"
                )
            if prerequisite_id == node_id:
                raise ValueError(f"{label} cannot contain a self dependency: {node_id}")
            visit(prerequisite_id)
        state[node_id] = 2

    for node_id in known:
        visit(node_id)


class CanvasModule(AgenticContract):
    id: ModuleId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    position: int = Field(ge=0)
    estimated_minutes: int = Field(gt=0, le=100_000)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    prerequisite_module_ids: list[ModuleId]


class CanvasLesson(AgenticContract):
    id: LessonId
    module_id: ModuleId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    content_markdown: str = Field(min_length=1, max_length=100_000)
    position: int = Field(ge=0)
    estimated_minutes: int = Field(gt=0, le=480)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    prerequisite_lesson_ids: list[LessonId]


class CanvasTestQuestion(AgenticContract):
    id: QuestionId
    kind: Literal["single_choice", "multiple_choice", "short_answer"]
    prompt: str = Field(min_length=1, max_length=4000)
    options: list[AnswerOption]
    correct_option_ids: list[OptionId]
    expected_answer: str | None = Field(max_length=8000)
    explanation: str = Field(min_length=1, max_length=4000)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_answer(self):
        option_ids = [item.id for item in self.options]
        _require_unique(f"question {self.id} option ids", option_ids)
        _require_unique(
            f"question {self.id} correct option ids", self.correct_option_ids
        )
        _require_known(
            f"question {self.id} correct option ids",
            self.correct_option_ids,
            set(option_ids),
        )
        if self.kind == "single_choice":
            if len(self.options) < 2 or len(self.correct_option_ids) != 1:
                raise ValueError(
                    "single_choice requires at least two options and one correct option"
                )
            if self.expected_answer is not None:
                raise ValueError("single_choice must not define expected_answer")
        elif self.kind == "multiple_choice":
            if len(self.options) < 2 or not self.correct_option_ids:
                raise ValueError(
                    "multiple_choice requires at least two options and correct options"
                )
            if self.expected_answer is not None:
                raise ValueError("multiple_choice must not define expected_answer")
        elif self.options or self.correct_option_ids or not self.expected_answer:
            raise ValueError(
                "short_answer requires expected_answer and must not define options"
            )
        return self


class CanvasTest(AgenticContract):
    id: CanvasTestId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    scope: Literal["module", "final"]
    module_id: ModuleId | None
    position: int = Field(ge=0)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    questions: list[CanvasTestQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope_and_aggregates(self):
        if self.scope == "module" and self.module_id is None:
            raise ValueError("module test requires module_id")
        if self.scope == "final" and self.module_id is not None:
            raise ValueError("final test must not define module_id")
        _require_unique(
            f"test {self.id} question ids", [item.id for item in self.questions]
        )
        _require_exact(
            f"test {self.id} objective ids",
            self.objective_ids,
            {value for item in self.questions for value in item.objective_ids},
        )
        _require_exact(
            f"test {self.id} competency ids",
            self.competency_ids,
            {value for item in self.questions for value in item.competency_ids},
        )
        _require_exact(
            f"test {self.id} source ref ids",
            self.source_ref_ids,
            {value for item in self.questions for value in item.source_ref_ids},
        )
        return self


class CanvasAssignmentBase(AgenticContract):
    lesson_id: LessonId
    position: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=255)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    rubric: AssessmentRubric


class CanvasPracticeAssignment(CanvasAssignmentBase):
    kind: Literal["practice"]
    id: PracticeId
    instructions: str = Field(min_length=1, max_length=8000)
    deliverable: str = Field(min_length=1, max_length=2000)


class CanvasCaseAssignment(CanvasAssignmentBase):
    kind: Literal["case"]
    id: CaseId
    scenario: str = Field(min_length=1, max_length=12_000)
    prompts: list[str] = Field(min_length=1)
    expected_response: str = Field(min_length=1, max_length=12_000)


CanvasAssignment = Annotated[
    CanvasPracticeAssignment | CanvasCaseAssignment, Field(discriminator="kind")
]


class CanvasGenerationPayload(AgenticContract):
    """Versioned semantic AI response converted by backend into canvas graph JSON."""

    schema_version: Literal["1.0"]
    course_plan_id: PlanId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    language: Literal["ru", "en"]
    difficulty: Literal["internship", "basic", "intermediate", "advanced"]
    estimated_minutes: int = Field(gt=0, le=100_000)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    modules: list[CanvasModule] = Field(min_length=1)
    lessons: list[CanvasLesson] = Field(min_length=1)
    tests: list[CanvasTest]
    assignments: list[CanvasAssignment]

    @model_validator(mode="after")
    def validate_canvas(self):
        for label, values in (
            ("payload objective ids", self.objective_ids),
            ("payload competency ids", self.competency_ids),
            ("payload source ref ids", self.source_ref_ids),
            ("module ids", [item.id for item in self.modules]),
            ("lesson ids", [item.id for item in self.lessons]),
            ("test ids", [item.id for item in self.tests]),
            ("assignment ids", [item.id for item in self.assignments]),
            (
                "question ids",
                [question.id for test in self.tests for question in test.questions],
            ),
            ("rubric ids", [item.rubric.id for item in self.assignments]),
        ):
            _require_unique(label, values)

        objective_ids = set(self.objective_ids)
        competency_ids = set(self.competency_ids)
        source_ref_ids = set(self.source_ref_ids)
        module_ids = {item.id for item in self.modules}
        lesson_ids = {item.id for item in self.lessons}

        _require_contiguous_positions("module positions", self.modules)
        lessons_by_module: dict[str, list[CanvasLesson]] = defaultdict(list)
        for lesson in self.lessons:
            _require_known("lesson module_id", [lesson.module_id], module_ids)
            lessons_by_module[lesson.module_id].append(lesson)
        for module in self.modules:
            module_lessons = lessons_by_module[module.id]
            if not module_lessons:
                raise ValueError(f"module {module.id} must contain at least one lesson")
            _require_contiguous_positions(
                f"module {module.id} lesson positions", module_lessons
            )
            expected_minutes = sum(item.estimated_minutes for item in module_lessons)
            if module.estimated_minutes != expected_minutes:
                raise ValueError(
                    f"module {module.id} estimated_minutes must equal lesson total "
                    f"{expected_minutes}"
                )

        if self.estimated_minutes != sum(item.estimated_minutes for item in self.lessons):
            raise ValueError("estimated_minutes must equal the total lesson duration")

        tests_by_target: dict[str, list[CanvasTest]] = defaultdict(list)
        for test in self.tests:
            if test.module_id is not None:
                _require_known(f"test {test.id} module_id", [test.module_id], module_ids)
            tests_by_target[test.module_id or "final"].append(test)
        for target_id, tests in tests_by_target.items():
            _require_contiguous_positions(f"{target_id} test positions", tests)

        assignments_by_lesson: dict[str, list[CanvasAssignmentBase]] = defaultdict(list)
        for assignment in self.assignments:
            _require_known(
                f"assignment {assignment.id} lesson_id",
                [assignment.lesson_id],
                lesson_ids,
            )
            assignments_by_lesson[assignment.lesson_id].append(assignment)
        for lesson_id, assignments in assignments_by_lesson.items():
            _require_contiguous_positions(
                f"lesson {lesson_id} assignment positions", assignments
            )

        for item in [*self.modules, *self.lessons, *self.tests, *self.assignments]:
            _require_unique(f"{item.id} objective ids", item.objective_ids)
            _require_unique(f"{item.id} competency ids", item.competency_ids)
            _require_unique(f"{item.id} source ref ids", item.source_ref_ids)
            _require_known(f"{item.id} objective ids", item.objective_ids, objective_ids)
            _require_known(
                f"{item.id} competency ids", item.competency_ids, competency_ids
            )
            _require_known(
                f"{item.id} source ref ids", item.source_ref_ids, source_ref_ids
            )

        for test in self.tests:
            for question in test.questions:
                _require_known(
                    f"{question.id} objective ids", question.objective_ids, objective_ids
                )
                _require_known(
                    f"{question.id} competency ids",
                    question.competency_ids,
                    competency_ids,
                )
                _require_known(
                    f"{question.id} source ref ids",
                    question.source_ref_ids,
                    source_ref_ids,
                )

        for assignment in self.assignments:
            rubric = assignment.rubric
            _require_known(
                f"{rubric.id} objective ids", rubric.objective_ids, objective_ids
            )
            _require_known(
                f"{rubric.id} competency ids", rubric.competency_ids, competency_ids
            )
            _require_known(
                f"{rubric.id} source ref ids", rubric.source_ref_ids, source_ref_ids
            )

        _validate_dag(
            "module prerequisites",
            {item.id: item.prerequisite_module_ids for item in self.modules},
        )
        _validate_dag(
            "lesson prerequisites",
            {item.id: item.prerequisite_lesson_ids for item in self.lessons},
        )
        return self

    def json_payload(self) -> tuple[list[dict], list[dict]]:
        """Convert semantic response into the current persisted graph contract."""
        modules = sorted(self.modules, key=lambda item: item.position)
        lessons = sorted(
            self.lessons,
            key=lambda item: (
                next(module.position for module in modules if module.id == item.module_id),
                item.position,
            ),
        )
        assignments_by_lesson: dict[str, list[CanvasAssignment]] = defaultdict(list)
        for assignment in self.assignments:
            assignments_by_lesson[assignment.lesson_id].append(assignment)
        for assignments in assignments_by_lesson.values():
            assignments.sort(key=lambda item: item.position)

        nodes: list[dict] = []
        edges: list[dict] = []
        for module in modules:
            nodes.append(
                {
                    "id": module.id,
                    "label": module.title,
                    "description": module.description,
                    "type": "module",
                    "estimated_minutes": module.estimated_minutes,
                    "objective_ids": module.objective_ids,
                    "competency_ids": module.competency_ids,
                    "source_refs": module.source_ref_ids,
                }
            )

        lessons_by_module: dict[str, list[CanvasLesson]] = defaultdict(list)
        for lesson in lessons:
            lessons_by_module[lesson.module_id].append(lesson)
            assignments = assignments_by_lesson.get(lesson.id, [])
            practice_payloads = []
            case_payloads = []
            assignment_refs: set[str] = set()
            for assignment in assignments:
                payload = assignment.model_dump(mode="json")
                payload.pop("kind")
                payload["rubric_id"] = assignment.rubric.id
                assignment_refs.update(assignment.source_ref_ids)
                assignment_refs.update(assignment.rubric.source_ref_ids)
                if isinstance(assignment, CanvasPracticeAssignment):
                    practice_payloads.append(payload)
                else:
                    payload["lesson_ids"] = [payload.pop("lesson_id")]
                    case_payloads.append(payload)
            nodes.append(
                {
                    "id": lesson.id,
                    "label": lesson.title,
                    "description": lesson.description,
                    "content": lesson.content_markdown,
                    "type": "lesson",
                    "estimated_minutes": lesson.estimated_minutes,
                    "objective_ids": lesson.objective_ids,
                    "competency_ids": lesson.competency_ids,
                    "source_refs": sorted(
                        set(lesson.source_ref_ids) | assignment_refs
                    ),
                    "practices": practice_payloads,
                    "cases": case_payloads,
                }
            )

        for module in modules:
            module_lessons = sorted(
                lessons_by_module[module.id], key=lambda item: item.position
            )
            for lesson in module_lessons:
                edges.append(
                    {"source": module.id, "target": lesson.id, "relation": "contains"}
                )
            for previous, current in zip(module_lessons, module_lessons[1:]):
                edges.append(
                    {
                        "source": previous.id,
                        "target": current.id,
                        "relation": "precedes",
                    }
                )
        for previous, current in zip(modules, modules[1:]):
            edges.append(
                {
                    "source": previous.id,
                    "target": current.id,
                    "relation": "precedes",
                }
            )
        for module in modules:
            for prerequisite_id in module.prerequisite_module_ids:
                edges.append(
                    {
                        "source": prerequisite_id,
                        "target": module.id,
                        "relation": "requires",
                    }
                )
        for lesson in lessons:
            for prerequisite_id in lesson.prerequisite_lesson_ids:
                edges.append(
                    {
                        "source": prerequisite_id,
                        "target": lesson.id,
                        "relation": "requires",
                    }
                )

        module_position = {item.id: item.position for item in modules}
        tests = sorted(
            self.tests,
            key=lambda item: (
                1 if item.scope == "final" else 0,
                module_position.get(item.module_id, len(modules)),
                item.position,
            ),
        )
        for test in tests:
            questions = []
            for question in test.questions:
                option_by_id = {item.id: item.text for item in question.options}
                correct_answer = (
                    " | ".join(
                        option_by_id[item] for item in question.correct_option_ids
                    )
                    if question.correct_option_ids
                    else question.expected_answer or ""
                )
                questions.append(
                    {
                        "id": question.id,
                        "kind": question.kind,
                        "question": question.prompt,
                        "answers": [item.text for item in question.options],
                        "correct_answer": correct_answer,
                        "explanation": question.explanation,
                        "objective_ids": question.objective_ids,
                        "competency_ids": question.competency_ids,
                        "source_refs": question.source_ref_ids,
                    }
                )
            nodes.append(
                {
                    "id": test.id,
                    "label": test.title,
                    "description": test.description,
                    "type": "test",
                    "assessment_scope": test.scope,
                    "objective_ids": test.objective_ids,
                    "competency_ids": test.competency_ids,
                    "source_refs": test.source_ref_ids,
                    "questions": questions,
                }
            )
            if test.scope == "module":
                edges.append(
                    {
                        "source": test.module_id,
                        "target": test.id,
                        "relation": "contains",
                    }
                )
            else:
                for module in modules:
                    edges.append(
                        {
                            "source": module.id,
                            "target": test.id,
                            "relation": "precedes",
                        }
                    )
        return nodes, edges
