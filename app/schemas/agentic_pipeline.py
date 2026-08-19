from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgenticContract(BaseModel):
    """Common validation policy for persisted and LLM-produced artifacts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


SourceRefId = Annotated[
    str, Field(pattern=r"^src:[a-z0-9][a-z0-9._:-]{2,127}$", max_length=128)
]
KnowledgeItemId = Annotated[
    str, Field(pattern=r"^kn:[a-z0-9][a-z0-9._:-]{1,125}$", max_length=128)
]
RoleId = Annotated[
    str, Field(pattern=r"^role:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]
CompetencyId = Annotated[
    str, Field(pattern=r"^cmp:[a-z0-9][a-z0-9._:-]{1,124}$", max_length=128)
]
SkillId = Annotated[
    str, Field(pattern=r"^skill:[a-z0-9][a-z0-9._:-]{1,122}$", max_length=128)
]
KnowledgeId = Annotated[
    str, Field(pattern=r"^know:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]
ProcedureId = Annotated[
    str, Field(pattern=r"^proc:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]
PlanId = Annotated[
    str, Field(pattern=r"^plan:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]
ModuleId = Annotated[
    str, Field(pattern=r"^mod:[a-z0-9][a-z0-9._:-]{1,124}$", max_length=128)
]
LessonId = Annotated[
    str, Field(pattern=r"^lesson:[a-z0-9][a-z0-9._:-]{1,121}$", max_length=128)
]
ObjectiveId = Annotated[
    str, Field(pattern=r"^obj:[a-z0-9][a-z0-9._:-]{1,124}$", max_length=128)
]
SectionId = Annotated[
    str, Field(pattern=r"^section:[a-z0-9][a-z0-9._:-]{1,120}$", max_length=128)
]
QuestionId = Annotated[
    str, Field(pattern=r"^question:[a-z0-9][a-z0-9._:-]{1,119}$", max_length=128)
]
OptionId = Annotated[
    str, Field(pattern=r"^option:[a-z0-9][a-z0-9._:-]{1,121}$", max_length=128)
]
PracticeId = Annotated[
    str, Field(pattern=r"^practice:[a-z0-9][a-z0-9._:-]{1,119}$", max_length=128)
]
CaseId = Annotated[
    str, Field(pattern=r"^case:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]
RubricId = Annotated[
    str, Field(pattern=r"^rubric:[a-z0-9][a-z0-9._:-]{1,121}$", max_length=128)
]
CriterionId = Annotated[
    str, Field(pattern=r"^criterion:[a-z0-9][a-z0-9._:-]{1,118}$", max_length=128)
]
LevelId = Annotated[
    str, Field(pattern=r"^level:[a-z0-9][a-z0-9._:-]{1,122}$", max_length=128)
]
IssueId = Annotated[
    str, Field(pattern=r"^issue:[a-z0-9][a-z0-9._:-]{1,122}$", max_length=128)
]
ImpactId = Annotated[
    str, Field(pattern=r"^impact:[a-z0-9][a-z0-9._:-]{1,121}$", max_length=128)
]
DiffId = Annotated[
    str, Field(pattern=r"^diff:[a-z0-9][a-z0-9._:-]{1,123}$", max_length=128)
]
GenericLogicalId = Annotated[
    str,
    Field(
        pattern=(
            r"^(plan|mod|lesson|obj|role|cmp|skill|know|proc|question|practice|"
            r"case|rubric|criterion|section):[a-z0-9][a-z0-9._:-]{1,120}$"
        ),
        max_length=128,
    ),
]


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _require_unique(label: str, values: list[str]) -> None:
    duplicates = _duplicates(values)
    if duplicates:
        raise ValueError(f"{label} must be unique; duplicates: {duplicates}")


def _require_known(label: str, values: list[str], known: set[str]) -> None:
    unknown = sorted(set(values) - known)
    if unknown:
        raise ValueError(f"{label} reference unknown ids: {unknown}")


def _require_exact(label: str, actual: list[str], expected: set[str]) -> None:
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    if missing or unexpected:
        raise ValueError(
            f"{label} must match exactly; missing={missing}, unexpected={unexpected}"
        )


def _validate_dag(label: str, node_ids: set[str], dependencies: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        marker = state.get(node_id, 0)
        if marker == 1:
            raise ValueError(f"{label} contains a cycle at {node_id}")
        if marker == 2:
            return
        state[node_id] = 1
        for dependency_id in dependencies.get(node_id, []):
            if dependency_id not in node_ids:
                raise ValueError(
                    f"{label} reference unknown prerequisite id: {dependency_id}"
                )
            if dependency_id == node_id:
                raise ValueError(f"{label} cannot contain a self dependency: {node_id}")
            visit(dependency_id)
        state[node_id] = 2

    for candidate in node_ids:
        visit(candidate)


def _source_map(source_refs: list[SourceRef]) -> dict[str, SourceRef]:
    _require_unique("source ref ids", [item.id for item in source_refs])
    return {item.id: item for item in source_refs}


def _validate_citations(
    source_refs: list[SourceRef], citation_groups: list[tuple[str, list[str]]]
) -> None:
    known = set(_source_map(source_refs))
    for label, citation_ids in citation_groups:
        _require_unique(f"{label} source_ref_ids", citation_ids)
        _require_known(f"{label} source_ref_ids", citation_ids, known)


class SourceRef(AgenticContract):
    id: SourceRefId
    document_id: int = Field(gt=0)
    document_version: int = Field(gt=0)
    document_content_hash: str = Field(min_length=1, max_length=128)
    chunk_id: int = Field(gt=0)
    chunk_index: int = Field(ge=0)
    page: int | None = Field(default=None, gt=0)
    section: str | None = Field(default=None, max_length=512)
    quote: str = Field(min_length=1, max_length=4000)


KnowledgeKind = Literal[
    "regulation", "process", "role", "term", "requirement", "procedure"
]


class KnowledgeItem(AgenticContract):
    id: KnowledgeItemId
    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=255)
    statement: str = Field(min_length=1, max_length=8000)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    related_item_ids: list[KnowledgeItemId] = Field(default_factory=list)
    attributes: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict
    )


class DocumentKnowledge(AgenticContract):
    document_id: int = Field(gt=0)
    document_version: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=8000)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    knowledge_item_ids: list[KnowledgeItemId] = Field(default_factory=list)


class IngestionArtifact(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    source_refs: list[SourceRef] = Field(min_length=1)
    documents: list[DocumentKnowledge] = Field(min_length=1)
    knowledge_items: list[KnowledgeItem] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_integrity(self):
        sources = _source_map(self.source_refs)
        items = {item.id: item for item in self.knowledge_items}
        _require_unique("knowledge item ids", [item.id for item in self.knowledge_items])
        _require_unique(
            "document versions",
            [f"{item.document_id}:v{item.document_version}" for item in self.documents],
        )

        citation_groups = [
            (f"knowledge item {item.id}", item.source_ref_ids)
            for item in self.knowledge_items
        ]
        citation_groups.extend(
            (f"document {item.document_id}:v{item.document_version}", item.source_ref_ids)
            for item in self.documents
        )
        _validate_citations(self.source_refs, citation_groups)

        assignments: Counter[str] = Counter()
        for document in self.documents:
            _require_unique(
                f"document {document.document_id} knowledge_item_ids",
                document.knowledge_item_ids,
            )
            _require_known(
                f"document {document.document_id} knowledge_item_ids",
                document.knowledge_item_ids,
                set(items),
            )
            for source_ref_id in document.source_ref_ids:
                source = sources[source_ref_id]
                if (
                    source.document_id != document.document_id
                    or source.document_version != document.document_version
                ):
                    raise ValueError(
                        f"document {document.document_id} contains a citation from another version"
                    )
            for item_id in document.knowledge_item_ids:
                assignments[item_id] += 1
                item_source_refs = [sources[value] for value in items[item_id].source_ref_ids]
                if not any(
                    source.document_id == document.document_id
                    and source.document_version == document.document_version
                    for source in item_source_refs
                ):
                    raise ValueError(
                        f"knowledge item {item_id} is assigned to a document it does not cite"
                    )

        unassigned = sorted(set(items) - set(assignments))
        if unassigned:
            raise ValueError(f"knowledge items must belong to a document: {unassigned}")
        for item in self.knowledge_items:
            _require_unique(f"knowledge item {item.id} related ids", item.related_item_ids)
            _require_known(
                f"knowledge item {item.id} related ids",
                item.related_item_ids,
                set(items),
            )
            if item.id in item.related_item_ids:
                raise ValueError(f"knowledge item {item.id} cannot relate to itself")
        return self


class RoleDefinition(AgenticContract):
    id: RoleId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class CompetencyDefinition(AgenticContract):
    id: CompetencyId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)
    level: Literal["awareness", "basic", "intermediate", "advanced", "expert"]
    skill_ids: list[SkillId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class SkillDefinition(AgenticContract):
    id: SkillId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)
    knowledge_ids: list[KnowledgeId] = Field(default_factory=list)
    procedure_ids: list[ProcedureId] = Field(default_factory=list)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_learning_content(self):
        if not self.knowledge_ids and not self.procedure_ids:
            raise ValueError("a skill must reference knowledge or procedures")
        return self


class KnowledgeDefinition(AgenticContract):
    id: KnowledgeId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)
    source_knowledge_item_ids: list[KnowledgeItemId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class ProcedureDefinition(AgenticContract):
    id: ProcedureId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4000)
    steps: list[str] = Field(min_length=1)
    source_knowledge_item_ids: list[KnowledgeItemId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class CompetencyMapArtifact(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    source_refs: list[SourceRef] = Field(min_length=1)
    source_knowledge_item_ids: list[KnowledgeItemId] = Field(min_length=1)
    roles: list[RoleDefinition] = Field(min_length=1)
    competencies: list[CompetencyDefinition] = Field(min_length=1)
    skills: list[SkillDefinition] = Field(min_length=1)
    knowledge: list[KnowledgeDefinition] = Field(min_length=1)
    procedures: list[ProcedureDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chain(self):
        collections = (
            ("role ids", self.roles),
            ("competency ids", self.competencies),
            ("skill ids", self.skills),
            ("knowledge ids", self.knowledge),
            ("procedure ids", self.procedures),
        )
        for label, values in collections:
            _require_unique(label, [item.id for item in values])
        _require_unique("source knowledge item ids", self.source_knowledge_item_ids)

        competency_ids = {item.id for item in self.competencies}
        skill_ids = {item.id for item in self.skills}
        knowledge_ids = {item.id for item in self.knowledge}
        procedure_ids = {item.id for item in self.procedures}
        competency_assignments: Counter[str] = Counter()
        skill_assignments: Counter[str] = Counter()
        knowledge_assignments: Counter[str] = Counter()
        procedure_assignments: Counter[str] = Counter()

        for role in self.roles:
            _require_unique(f"role {role.id} competency ids", role.competency_ids)
            _require_known(f"role {role.id} competency ids", role.competency_ids, competency_ids)
            competency_assignments.update(role.competency_ids)
        for competency in self.competencies:
            _require_unique(f"competency {competency.id} skill ids", competency.skill_ids)
            _require_known(f"competency {competency.id} skill ids", competency.skill_ids, skill_ids)
            skill_assignments.update(competency.skill_ids)
        for skill in self.skills:
            _require_unique(f"skill {skill.id} knowledge ids", skill.knowledge_ids)
            _require_unique(f"skill {skill.id} procedure ids", skill.procedure_ids)
            _require_known(f"skill {skill.id} knowledge ids", skill.knowledge_ids, knowledge_ids)
            _require_known(f"skill {skill.id} procedure ids", skill.procedure_ids, procedure_ids)
            knowledge_assignments.update(skill.knowledge_ids)
            procedure_assignments.update(skill.procedure_ids)

        for label, known, assigned in (
            ("competencies", competency_ids, competency_assignments),
            ("skills", skill_ids, skill_assignments),
            ("knowledge", knowledge_ids, knowledge_assignments),
            ("procedures", procedure_ids, procedure_assignments),
        ):
            unassigned = sorted(known - set(assigned))
            if unassigned:
                raise ValueError(f"{label} must be connected to the competency chain: {unassigned}")

        for item in [*self.knowledge, *self.procedures]:
            _require_unique(
                f"{item.id} source knowledge item ids", item.source_knowledge_item_ids
            )
            _require_known(
                f"{item.id} source knowledge item ids",
                item.source_knowledge_item_ids,
                set(self.source_knowledge_item_ids),
            )
        _validate_citations(
            self.source_refs,
            [
                (item.id, item.source_ref_ids)
                for item in [
                    *self.roles,
                    *self.competencies,
                    *self.skills,
                    *self.knowledge,
                    *self.procedures,
                ]
            ],
        )
        return self


class CourseObjective(AgenticContract):
    id: ObjectiveId
    text: str = Field(min_length=1, max_length=2000)
    bloom_level: Literal[
        "remember", "understand", "apply", "analyze", "evaluate", "create"
    ]
    measurable_verb: str = Field(min_length=1, max_length=128)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class PlannedLesson(AgenticContract):
    id: LessonId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    estimated_minutes: int = Field(gt=0, le=480)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    prerequisite_lesson_ids: list[LessonId] = Field(default_factory=list)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class PlannedModule(AgenticContract):
    id: ModuleId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    lesson_ids: list[LessonId] = Field(min_length=1)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    prerequisite_module_ids: list[ModuleId] = Field(default_factory=list)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class CoursePlan(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    id: PlanId
    title: str = Field(min_length=1, max_length=255)
    goal: str = Field(min_length=1, max_length=2000)
    target_audience: str = Field(min_length=1, max_length=1000)
    difficulty: Literal["internship", "basic", "intermediate", "advanced"]
    language: Literal["ru", "en"]
    estimated_minutes: int = Field(gt=0)
    source_refs: list[SourceRef] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    objectives: list[CourseObjective] = Field(min_length=1)
    modules: list[PlannedModule] = Field(min_length=1)
    lessons: list[PlannedLesson] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan(self):
        _require_unique("course competency ids", self.competency_ids)
        _require_unique("objective ids", [item.id for item in self.objectives])
        _require_unique("module ids", [item.id for item in self.modules])
        _require_unique("lesson ids", [item.id for item in self.lessons])
        objective_ids = {item.id for item in self.objectives}
        module_ids = {item.id for item in self.modules}
        lesson_ids = {item.id for item in self.lessons}
        competency_ids = set(self.competency_ids)
        competency_assignments: Counter[str] = Counter()

        for objective in self.objectives:
            _require_unique(
                f"objective {objective.id} competency ids", objective.competency_ids
            )
            _require_known(
                f"objective {objective.id} competency ids",
                objective.competency_ids,
                competency_ids,
            )
            competency_assignments.update(objective.competency_ids)

        unused_competencies = sorted(competency_ids - set(competency_assignments))
        if unused_competencies:
            raise ValueError(
                f"every course competency must be covered by an objective: {unused_competencies}"
            )

        lesson_assignments: Counter[str] = Counter()
        objective_assignments: Counter[str] = Counter()
        for module in self.modules:
            _require_unique(f"module {module.id} lesson ids", module.lesson_ids)
            _require_unique(f"module {module.id} objective ids", module.objective_ids)
            _require_unique(f"module {module.id} competency ids", module.competency_ids)
            _require_unique(
                f"module {module.id} prerequisite ids", module.prerequisite_module_ids
            )
            _require_known(f"module {module.id} lesson ids", module.lesson_ids, lesson_ids)
            _require_known(
                f"module {module.id} objective ids", module.objective_ids, objective_ids
            )
            _require_known(
                f"module {module.id} competency ids",
                module.competency_ids,
                competency_ids,
            )
            lesson_assignments.update(module.lesson_ids)

        invalid_assignments = sorted(
            item_id for item_id, count in lesson_assignments.items() if count != 1
        )
        missing_lessons = sorted(lesson_ids - set(lesson_assignments))
        if invalid_assignments or missing_lessons:
            raise ValueError(
                "every lesson must belong to exactly one module; "
                f"multiply_assigned={invalid_assignments}, missing={missing_lessons}"
            )

        for lesson in self.lessons:
            _require_unique(f"lesson {lesson.id} objective ids", lesson.objective_ids)
            _require_unique(f"lesson {lesson.id} competency ids", lesson.competency_ids)
            _require_unique(
                f"lesson {lesson.id} prerequisite ids", lesson.prerequisite_lesson_ids
            )
            _require_known(
                f"lesson {lesson.id} objective ids", lesson.objective_ids, objective_ids
            )
            _require_known(
                f"lesson {lesson.id} competency ids",
                lesson.competency_ids,
                competency_ids,
            )
            objective_assignments.update(lesson.objective_ids)

        lessons_by_id = {item.id: item for item in self.lessons}
        for module in self.modules:
            taught_objectives = {
                objective_id
                for lesson_id in module.lesson_ids
                for objective_id in lessons_by_id[lesson_id].objective_ids
            }
            taught_competencies = {
                competency_id
                for lesson_id in module.lesson_ids
                for competency_id in lessons_by_id[lesson_id].competency_ids
            }
            _require_exact(
                f"module {module.id} objective ids",
                module.objective_ids,
                taught_objectives,
            )
            _require_exact(
                f"module {module.id} competency ids",
                module.competency_ids,
                taught_competencies,
            )

        missing_objectives = sorted(objective_ids - set(objective_assignments))
        if missing_objectives:
            raise ValueError(
                f"every objective must be taught by a lesson: {missing_objectives}"
            )
        _validate_dag(
            "module prerequisites",
            module_ids,
            {item.id: item.prerequisite_module_ids for item in self.modules},
        )
        _validate_dag(
            "lesson prerequisites",
            lesson_ids,
            {item.id: item.prerequisite_lesson_ids for item in self.lessons},
        )

        expected_minutes = sum(item.estimated_minutes for item in self.lessons)
        if self.estimated_minutes != expected_minutes:
            raise ValueError(
                f"estimated_minutes must equal lesson total {expected_minutes}"
            )

        cited = [
            *((item.id, item.source_ref_ids) for item in self.objectives),
            *((item.id, item.source_ref_ids) for item in self.modules),
            *((item.id, item.source_ref_ids) for item in self.lessons),
        ]
        _validate_citations(self.source_refs, cited)
        return self


class LessonSection(AgenticContract):
    id: SectionId
    heading: str = Field(min_length=1, max_length=255)
    content_markdown: str = Field(min_length=1, max_length=50000)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class LessonDraft(AgenticContract):
    id: LessonId
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    sections: list[LessonSection] = Field(min_length=1)
    key_takeaways: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sections(self):
        _require_unique(f"lesson {self.id} section ids", [item.id for item in self.sections])
        _require_unique(f"lesson {self.id} objective ids", self.objective_ids)
        _require_unique(f"lesson {self.id} competency ids", self.competency_ids)
        _require_unique(f"lesson {self.id} source refs", self.source_ref_ids)
        section_refs = {ref for section in self.sections for ref in section.source_ref_ids}
        if section_refs != set(self.source_ref_ids):
            raise ValueError(
                f"lesson {self.id} source_ref_ids must equal the union of section citations"
            )
        return self


class WriterArtifact(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    source_refs: list[SourceRef] = Field(min_length=1)
    expected_lesson_ids: list[LessonId] = Field(min_length=1)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    lessons: list[LessonDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_drafts(self):
        _require_unique("expected lesson ids", self.expected_lesson_ids)
        _require_unique("writer objective ids", self.objective_ids)
        _require_unique("writer competency ids", self.competency_ids)
        _require_unique("lesson draft ids", [item.id for item in self.lessons])
        _require_exact(
            "lesson draft ids", [item.id for item in self.lessons], set(self.expected_lesson_ids)
        )
        for lesson in self.lessons:
            _require_known(
                f"lesson {lesson.id} objective ids",
                lesson.objective_ids,
                set(self.objective_ids),
            )
            _require_known(
                f"lesson {lesson.id} competency ids",
                lesson.competency_ids,
                set(self.competency_ids),
            )
        _validate_citations(
            self.source_refs,
            [
                *((lesson.id, lesson.source_ref_ids) for lesson in self.lessons),
                *(
                    (section.id, section.source_ref_ids)
                    for lesson in self.lessons
                    for section in lesson.sections
                ),
            ],
        )
        return self


class AnswerOption(AgenticContract):
    id: OptionId
    text: str = Field(min_length=1, max_length=2000)


class AssessmentQuestion(AgenticContract):
    id: QuestionId
    kind: Literal["single_choice", "multiple_choice", "short_answer"]
    scope: Literal["lesson", "module", "final"]
    target_id: GenericLogicalId
    prompt: str = Field(min_length=1, max_length=4000)
    options: list[AnswerOption] = Field(default_factory=list)
    correct_option_ids: list[OptionId] = Field(default_factory=list)
    expected_answer: str | None = Field(default=None, max_length=8000)
    explanation: str = Field(min_length=1, max_length=4000)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_answer(self):
        option_ids = [item.id for item in self.options]
        _require_unique(f"question {self.id} option ids", option_ids)
        _require_unique(f"question {self.id} correct option ids", self.correct_option_ids)
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
        else:
            if self.options or self.correct_option_ids or not self.expected_answer:
                raise ValueError(
                    "short_answer requires expected_answer and must not define options"
                )
        return self


class RubricLevel(AgenticContract):
    id: LevelId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    score: float = Field(ge=0)


class AssessmentCriterion(AgenticContract):
    id: CriterionId
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    weight: float = Field(gt=0, le=1)
    levels: list[RubricLevel] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_levels(self):
        _require_unique(f"criterion {self.id} level ids", [item.id for item in self.levels])
        scores = [item.score for item in self.levels]
        if len(scores) != len(set(scores)):
            raise ValueError(f"criterion {self.id} level scores must be unique")
        return self


class AssessmentRubric(AgenticContract):
    id: RubricId
    title: str = Field(min_length=1, max_length=255)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)
    criteria: list[AssessmentCriterion] = Field(min_length=1)
    passing_score: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_rubric(self):
        _require_unique(f"rubric {self.id} criterion ids", [item.id for item in self.criteria])
        total_weight = sum(item.weight for item in self.criteria)
        if abs(total_weight - 1.0) > 0.001:
            raise ValueError(f"rubric {self.id} criterion weights must sum to 1.0")
        maximum_score = sum(max(level.score for level in item.levels) for item in self.criteria)
        if self.passing_score > maximum_score:
            raise ValueError(
                f"rubric {self.id} passing_score exceeds maximum {maximum_score}"
            )
        return self


class PracticeAssignment(AgenticContract):
    id: PracticeId
    lesson_id: LessonId
    title: str = Field(min_length=1, max_length=255)
    instructions: str = Field(min_length=1, max_length=8000)
    deliverable: str = Field(min_length=1, max_length=2000)
    rubric_id: RubricId
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class CaseStudy(AgenticContract):
    id: CaseId
    title: str = Field(min_length=1, max_length=255)
    scenario: str = Field(min_length=1, max_length=12000)
    prompts: list[str] = Field(min_length=1)
    expected_response: str = Field(min_length=1, max_length=12000)
    lesson_ids: list[LessonId] = Field(min_length=1)
    rubric_id: RubricId
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    source_ref_ids: list[SourceRefId] = Field(min_length=1)


class AssessmentArtifact(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    course_plan_id: PlanId
    source_refs: list[SourceRef] = Field(min_length=1)
    module_ids: list[ModuleId] = Field(min_length=1)
    lesson_ids: list[LessonId] = Field(min_length=1)
    objective_ids: list[ObjectiveId] = Field(min_length=1)
    competency_ids: list[CompetencyId] = Field(min_length=1)
    questions: list[AssessmentQuestion] = Field(default_factory=list)
    practices: list[PracticeAssignment] = Field(default_factory=list)
    cases: list[CaseStudy] = Field(default_factory=list)
    rubrics: list[AssessmentRubric] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assessments(self):
        if not self.questions and not self.practices and not self.cases:
            raise ValueError("assessment artifact must contain an assessment")
        for label, values in (
            ("module ids", self.module_ids),
            ("lesson ids", self.lesson_ids),
            ("objective ids", self.objective_ids),
            ("competency ids", self.competency_ids),
        ):
            _require_unique(label, values)
        for label, values in (
            ("question ids", self.questions),
            ("practice ids", self.practices),
            ("case ids", self.cases),
            ("rubric ids", self.rubrics),
        ):
            _require_unique(label, [item.id for item in values])

        lesson_ids = set(self.lesson_ids)
        module_ids = set(self.module_ids)
        objective_ids = set(self.objective_ids)
        competency_ids = set(self.competency_ids)
        rubric_ids = {item.id for item in self.rubrics}
        used_rubric_ids: set[str] = set()

        for question in self.questions:
            expected_targets = (
                lesson_ids
                if question.scope == "lesson"
                else module_ids
                if question.scope == "module"
                else {self.course_plan_id}
            )
            _require_known(
                f"question {question.id} target", [question.target_id], expected_targets
            )
        for practice in self.practices:
            _require_known(f"practice {practice.id} lesson", [practice.lesson_id], lesson_ids)
            _require_known(f"practice {practice.id} rubric", [practice.rubric_id], rubric_ids)
            used_rubric_ids.add(practice.rubric_id)
        for case in self.cases:
            _require_unique(f"case {case.id} lesson ids", case.lesson_ids)
            _require_known(f"case {case.id} lesson ids", case.lesson_ids, lesson_ids)
            _require_known(f"case {case.id} rubric", [case.rubric_id], rubric_ids)
            used_rubric_ids.add(case.rubric_id)

        unused_rubrics = sorted(rubric_ids - used_rubric_ids)
        if unused_rubrics:
            raise ValueError(f"rubrics must be attached to a practice or case: {unused_rubrics}")

        all_items = [*self.questions, *self.practices, *self.cases, *self.rubrics]
        for item in all_items:
            _require_unique(f"{item.id} objective ids", item.objective_ids)
            _require_unique(f"{item.id} competency ids", item.competency_ids)
            _require_known(f"{item.id} objective ids", item.objective_ids, objective_ids)
            _require_known(f"{item.id} competency ids", item.competency_ids, competency_ids)
        _validate_citations(
            self.source_refs,
            [(item.id, item.source_ref_ids) for item in all_items],
        )
        return self


class QAIssue(AgenticContract):
    id: IssueId
    severity: Literal["blocker", "error", "warning", "info"]
    category: Literal[
        "hallucination",
        "citation",
        "coverage",
        "complexity",
        "consistency",
        "assessment",
        "pedagogy",
        "format",
    ]
    artifact_type: Literal[
        "ingestion",
        "competency_map",
        "course_plan",
        "lesson",
        "question",
        "practice",
        "case",
        "rubric",
    ]
    artifact_id: GenericLogicalId | None = None
    message: str = Field(min_length=1, max_length=4000)
    evidence_source_ref_ids: list[SourceRefId] = Field(default_factory=list)
    suggested_fix: str = Field(min_length=1, max_length=4000)
    auto_fixable: bool


class QAArtifact(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    source_refs: list[SourceRef] = Field(min_length=1)
    checked_artifact_ids: list[GenericLogicalId] = Field(min_length=1)
    issues: list[QAIssue] = Field(default_factory=list)
    verdict: Literal["pass", "revise", "fail"]
    coverage_score: float = Field(ge=0, le=1)
    grounding_score: float = Field(ge=0, le=1)
    difficulty_score: float = Field(ge=0, le=1)
    assessment_quality_score: float = Field(ge=0, le=1)
    revision_required_for: list[IssueId] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_report(self):
        _require_unique("checked artifact ids", self.checked_artifact_ids)
        _require_unique("QA issue ids", [item.id for item in self.issues])
        _require_unique("revision issue ids", self.revision_required_for)
        issue_ids = {item.id for item in self.issues}
        _require_known("revision issue ids", self.revision_required_for, issue_ids)
        _validate_citations(
            self.source_refs,
            [
                (f"QA issue {item.id}", item.evidence_source_ref_ids)
                for item in self.issues
            ],
        )
        severe_ids = {
            item.id for item in self.issues if item.severity in {"blocker", "error"}
        }
        if self.verdict == "pass" and severe_ids:
            raise ValueError("pass verdict cannot contain blocker or error issues")
        if self.verdict == "fail" and not severe_ids:
            raise ValueError("fail verdict requires a blocker or error issue")
        if self.verdict == "revise" and not self.revision_required_for:
            raise ValueError("revise verdict requires revision_required_for")
        if self.verdict == "pass" and self.revision_required_for:
            raise ValueError("pass verdict cannot require revisions")
        return self


class DocumentVersionChange(AgenticContract):
    document_id: int = Field(gt=0)
    change_type: Literal["added", "modified", "removed"]
    before_version: int | None = Field(default=None, gt=0)
    before_content_hash: str | None = Field(default=None, min_length=1, max_length=128)
    after_version: int | None = Field(default=None, gt=0)
    after_content_hash: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_versions(self):
        has_before = self.before_version is not None and self.before_content_hash is not None
        has_after = self.after_version is not None and self.after_content_hash is not None
        if self.change_type == "added" and (has_before or not has_after):
            raise ValueError("added document requires only an after version")
        if self.change_type == "removed" and (not has_before or has_after):
            raise ValueError("removed document requires only a before version")
        if self.change_type == "modified" and (not has_before or not has_after):
            raise ValueError("modified document requires before and after versions")
        if (self.before_version is None) != (self.before_content_hash is None):
            raise ValueError("before version and hash must be set together")
        if (self.after_version is None) != (self.after_content_hash is None):
            raise ValueError("after version and hash must be set together")
        return self


class UpdateImpact(AgenticContract):
    id: ImpactId
    affected_artifact_type: Literal[
        "competency",
        "objective",
        "module",
        "lesson",
        "question",
        "practice",
        "case",
        "rubric",
    ]
    affected_artifact_id: GenericLogicalId
    impact: Literal["none", "low", "medium", "high", "breaking"]
    proposed_action: Literal["no_change", "review", "regenerate", "archive"]
    reason: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    before_source_ref_ids: list[SourceRefId] = Field(default_factory=list)
    after_source_ref_ids: list[SourceRefId] = Field(default_factory=list)


class UpdateDiffOperation(AgenticContract):
    id: DiffId
    operation: Literal["add", "replace", "remove"]
    target_artifact_type: Literal[
        "competency",
        "objective",
        "module",
        "lesson",
        "question",
        "practice",
        "case",
        "rubric",
    ]
    target_artifact_id: GenericLogicalId
    json_pointer: str = Field(pattern=r"^(/([^/~]|~[01])*)*$", max_length=1000)
    before: Any | None = None
    after: Any | None = None
    impact_ids: list[ImpactId] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_values(self):
        fields = self.model_fields_set
        if self.operation == "add" and ("before" in fields or "after" not in fields):
            raise ValueError("add diff requires only an after value")
        if self.operation == "remove" and ("before" not in fields or "after" in fields):
            raise ValueError("remove diff requires only a before value")
        if self.operation == "replace" and not {"before", "after"}.issubset(fields):
            raise ValueError("replace diff requires before and after values")
        return self


class UpdateArtifact(AgenticContract):
    artifact_version: Literal["1.0"] = "1.0"
    previous_source_refs: list[SourceRef] = Field(default_factory=list)
    current_source_refs: list[SourceRef] = Field(default_factory=list)
    document_changes: list[DocumentVersionChange] = Field(min_length=1)
    impacts: list[UpdateImpact] = Field(default_factory=list)
    proposed_diff: list[UpdateDiffOperation] = Field(default_factory=list)
    requires_human_review: bool
    summary: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_update(self):
        if not self.previous_source_refs and not self.current_source_refs:
            raise ValueError("update artifact requires previous or current source refs")
        source_refs = [*self.previous_source_refs, *self.current_source_refs]
        source_by_id: dict[str, SourceRef] = {}
        for source in source_refs:
            existing = source_by_id.get(source.id)
            if existing is not None and existing != source:
                raise ValueError(f"source ref {source.id} has conflicting definitions")
            source_by_id[source.id] = source
        _require_unique(
            "document change ids",
            [str(item.document_id) for item in self.document_changes],
        )
        _require_unique("impact ids", [item.id for item in self.impacts])
        _require_unique("diff ids", [item.id for item in self.proposed_diff])
        known_source_ids = set(source_by_id)
        known_impact_ids = {item.id for item in self.impacts}
        for impact in self.impacts:
            _require_unique(f"impact {impact.id} before refs", impact.before_source_ref_ids)
            _require_unique(f"impact {impact.id} after refs", impact.after_source_ref_ids)
            _require_known(
                f"impact {impact.id} before refs",
                impact.before_source_ref_ids,
                known_source_ids,
            )
            _require_known(
                f"impact {impact.id} after refs",
                impact.after_source_ref_ids,
                known_source_ids,
            )
            if impact.impact == "none" and impact.proposed_action != "no_change":
                raise ValueError("an impact of none must use no_change")
            if impact.proposed_action != "no_change" and not (
                impact.before_source_ref_ids or impact.after_source_ref_ids
            ):
                raise ValueError(f"impact {impact.id} must cite changed sources")
        for operation in self.proposed_diff:
            _require_unique(f"diff {operation.id} impact ids", operation.impact_ids)
            _require_known(
                f"diff {operation.id} impact ids", operation.impact_ids, known_impact_ids
            )
            for impact_id in operation.impact_ids:
                impact = next(item for item in self.impacts if item.id == impact_id)
                if (
                    impact.affected_artifact_type != operation.target_artifact_type
                    or impact.affected_artifact_id != operation.target_artifact_id
                ):
                    raise ValueError(
                        f"diff {operation.id} target does not match impact {impact_id}"
                    )
        actionable = {
            item.id for item in self.impacts if item.proposed_action != "no_change"
        }
        covered = {
            impact_id for operation in self.proposed_diff for impact_id in operation.impact_ids
        }
        if covered - actionable:
            raise ValueError("diff operations cannot target no_change impacts")
        if self.proposed_diff and not self.requires_human_review:
            raise ValueError("a proposed diff must require human review")
        return self


class AgenticPipelineResult(AgenticContract):
    pipeline_version: Literal["1.0"] = "1.0"
    ingestion: IngestionArtifact
    competency_map: CompetencyMapArtifact
    course_plan: CoursePlan
    writer: WriterArtifact
    assessment: AssessmentArtifact
    qa: QAArtifact
    update: UpdateArtifact | None = None

    @model_validator(mode="after")
    def validate_cross_stage_contracts(self):
        canonical_sources = {item.id: item for item in self.ingestion.source_refs}

        for stage_name, sources in (
            ("competency_map", self.competency_map.source_refs),
            ("course_plan", self.course_plan.source_refs),
            ("writer", self.writer.source_refs),
            ("assessment", self.assessment.source_refs),
            ("qa", self.qa.source_refs),
        ):
            for source in sources:
                canonical = canonical_sources.get(source.id)
                if canonical is None:
                    raise ValueError(f"{stage_name} cites unknown ingestion source {source.id}")
                if canonical != source:
                    raise ValueError(
                        f"{stage_name} changed canonical source ref {source.id}"
                    )

        ingestion_item_ids = {item.id for item in self.ingestion.knowledge_items}
        _require_known(
            "competency map source knowledge items",
            self.competency_map.source_knowledge_item_ids,
            ingestion_item_ids,
        )
        competency_ids = {item.id for item in self.competency_map.competencies}
        _require_known(
            "course plan competency ids", self.course_plan.competency_ids, competency_ids
        )

        plan_lesson_ids = {item.id for item in self.course_plan.lessons}
        plan_objective_ids = {item.id for item in self.course_plan.objectives}
        plan_module_ids = {item.id for item in self.course_plan.modules}
        _require_exact(
            "writer expected lesson ids", self.writer.expected_lesson_ids, plan_lesson_ids
        )
        _require_exact("writer objective ids", self.writer.objective_ids, plan_objective_ids)
        _require_exact(
            "writer competency ids",
            self.writer.competency_ids,
            set(self.course_plan.competency_ids),
        )
        plans_by_lesson = {item.id: item for item in self.course_plan.lessons}
        for lesson in self.writer.lessons:
            planned = plans_by_lesson[lesson.id]
            _require_exact(
                f"writer lesson {lesson.id} objectives",
                lesson.objective_ids,
                set(planned.objective_ids),
            )
            _require_exact(
                f"writer lesson {lesson.id} competencies",
                lesson.competency_ids,
                set(planned.competency_ids),
            )

        if self.assessment.course_plan_id != self.course_plan.id:
            raise ValueError("assessment course_plan_id does not match course plan")
        _require_exact("assessment lesson ids", self.assessment.lesson_ids, plan_lesson_ids)
        _require_exact("assessment module ids", self.assessment.module_ids, plan_module_ids)
        _require_exact(
            "assessment objective ids", self.assessment.objective_ids, plan_objective_ids
        )
        _require_exact(
            "assessment competency ids",
            self.assessment.competency_ids,
            set(self.course_plan.competency_ids),
        )
        return self
