from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from app.core.config import settings
from app.schemas.agentic_pipeline import (
    AgenticPipelineResult,
    AssessmentArtifact,
    CompetencyMapArtifact,
    CoursePlan,
    IngestionArtifact,
    QAArtifact,
    SourceRef,
    WriterArtifact,
)
from app.schemas.pipeline import GeneratedGraphPayload
from app.services.agent_runtime import AgentRuntime, LegacyGraphResponse


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=lambda item: (
            item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item)
        ),
    )


def _source_ids(values: list[SourceRef]) -> list[str]:
    return [item.id for item in values]


def _canonical_sources(
    stage: str,
    actual: list[SourceRef],
    source_catalog: list[dict],
    *,
    require_all: bool = False,
) -> None:
    canonical = {item.id: item for item in map(SourceRef.model_validate, source_catalog)}
    for item in actual:
        if item.id not in canonical:
            raise ValueError(f"{stage} invented source ref {item.id}")
        if item != canonical[item.id]:
            raise ValueError(f"{stage} changed source ref {item.id}")
    if require_all and {item.id for item in actual} != set(canonical):
        missing = sorted(set(canonical) - {item.id for item in actual})
        raise ValueError(f"{stage} omitted source refs: {missing}")


@dataclass(frozen=True)
class AgenticGraphBuild:
    nodes: list[dict]
    edges: list[dict]
    result: AgenticPipelineResult | None
    legacy_fallback: bool = False

    @property
    def qa_summary(self) -> dict | None:
        if self.result is None:
            return None
        qa = self.result.qa
        return {
            "verdict": qa.verdict,
            "coverage_score": qa.coverage_score,
            "grounding_score": qa.grounding_score,
            "difficulty_score": qa.difficulty_score,
            "assessment_quality_score": qa.assessment_quality_score,
            "issue_count": len(qa.issues),
        }


class AgenticCoursePipeline:
    """Backend-only coordinator for the grounded course generation agents."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        checkpoint: Callable[[str, int], None],
    ) -> None:
        self.runtime = runtime
        self.checkpoint = checkpoint

    def run(
        self,
        *,
        course_title: str,
        settings_snapshot: dict,
        source_catalog: list[dict],
    ) -> AgenticGraphBuild:
        self.checkpoint("ingestion", 5)
        common = {
            "course_title": course_title,
            "goal": settings_snapshot["goal"],
            "target_audience": settings_snapshot.get("target_audience")
            or ("не указана" if settings_snapshot["language"] == "ru" else "not specified"),
            "difficulty": settings_snapshot["difficulty"],
            "language": settings_snapshot["language"],
            "lesson_count": settings_snapshot["lesson_count"],
        }
        try:
            ingestion = self.runtime.execute(
                agent="ingestion",
                artifact="document_knowledge",
                sequence=0,
                template_name="ingestion_agent_prompt.j2",
                response_model=IngestionArtifact,
                prompt_context={
                    **common,
                    "source_catalog_json": _json(source_catalog),
                },
                max_tokens=4096,
                # Old graph fakes are kept only for the existing test contract;
                # production can never bypass the QA gate this way.
                allow_legacy_graph=settings.ENV == "test",
            )
        except LegacyGraphResponse as legacy:
            graph = GeneratedGraphPayload.model_validate(legacy.payload)
            nodes, edges = graph.json_payload()
            return AgenticGraphBuild(
                nodes=nodes, edges=edges, result=None, legacy_fallback=True
            )
        _canonical_sources(
            "ingestion", ingestion.source_refs, source_catalog, require_all=True
        )

        self.checkpoint("competency_mapping", 20)
        competency_map = self.runtime.execute(
            agent="competency_mapper",
            artifact="competency_map",
            sequence=0,
            template_name="competency_mapper_prompt.j2",
            response_model=CompetencyMapArtifact,
            prompt_context={
                **common,
                "ingestion_artifact_json": _json(ingestion),
            },
            max_tokens=4096,
        )
        _canonical_sources(
            "competency_mapper", competency_map.source_refs, source_catalog
        )

        self.checkpoint("course_architecture", 35)
        course_plan = self.runtime.execute(
            agent="course_architect",
            artifact="course_plan",
            sequence=0,
            template_name="course_architect_prompt.j2",
            response_model=CoursePlan,
            prompt_context={
                **common,
                "competency_map_json": _json(competency_map),
                "source_catalog_json": _json(competency_map.source_refs),
            },
            max_tokens=4096,
        )
        if len(course_plan.lessons) != settings_snapshot["lesson_count"]:
            raise ValueError("Course Architect returned an unexpected lesson count")
        _canonical_sources("course_architect", course_plan.source_refs, source_catalog)

        self.checkpoint("lesson_writing", 50)
        writer = self._write_lessons(
            common=common,
            course_plan=course_plan,
            qa_feedback=None,
            revision=0,
        )

        self.checkpoint("assessment_generation", 72)
        assessment = self._create_assessments(
            common=common,
            settings_snapshot=settings_snapshot,
            course_plan=course_plan,
            writer=writer,
            qa_feedback=None,
            revision=0,
        )

        self.checkpoint("quality_assurance", 88)
        qa = self._review(
            common=common,
            ingestion=ingestion,
            competency_map=competency_map,
            course_plan=course_plan,
            writer=writer,
            assessment=assessment,
            revision=0,
        )

        if qa.verdict == "revise":
            writer = self._write_lessons(
                common=common,
                course_plan=course_plan,
                qa_feedback=qa,
                revision=1,
            )
            assessment = self._create_assessments(
                common=common,
                settings_snapshot=settings_snapshot,
                course_plan=course_plan,
                writer=writer,
                qa_feedback=qa,
                revision=1,
            )
            qa = self._review(
                common=common,
                ingestion=ingestion,
                competency_map=competency_map,
                course_plan=course_plan,
                writer=writer,
                assessment=assessment,
                revision=1,
            )

        if qa.verdict != "pass":
            raise ValueError(f"Critic QA rejected generation: {qa.summary}")

        result = AgenticPipelineResult(
            ingestion=ingestion,
            competency_map=competency_map,
            course_plan=course_plan,
            writer=writer,
            assessment=assessment,
            qa=qa,
        )
        nodes, edges = self._to_graph(result, settings_snapshot)
        self.checkpoint("materialization", 96)
        return AgenticGraphBuild(nodes=nodes, edges=edges, result=result)

    def _write_lessons(
        self,
        *,
        common: dict,
        course_plan: CoursePlan,
        qa_feedback: QAArtifact | None,
        revision: int,
    ) -> WriterArtifact:
        objective_by_id = {item.id: item for item in course_plan.objectives}
        drafts = []
        sources: dict[str, SourceRef] = {}
        objective_ids: set[str] = set()
        competency_ids: set[str] = set()
        for index, lesson in enumerate(course_plan.lessons):
            lesson_sources = [
                item for item in course_plan.source_refs if item.id in lesson.source_ref_ids
            ]
            objectives = [objective_by_id[item] for item in lesson.objective_ids]
            artifact = self.runtime.execute(
                agent="lesson_writer",
                artifact="lesson_draft",
                sequence=revision * 1000 + index,
                template_name="lesson_writer_prompt.j2",
                response_model=WriterArtifact,
                prompt_context={
                    **common,
                    "course_plan_json": _json(course_plan),
                    "lesson_spec_json": _json(lesson),
                    "lesson_objectives_json": _json(objectives),
                    "source_refs_json": _json(lesson_sources),
                    "lesson_ids_json": _json([lesson.id]),
                    "source_catalog_json": _json(lesson_sources),
                    "qa_feedback_json": _json(qa_feedback) if qa_feedback else "null",
                },
                max_tokens=4096,
            )
            if artifact.expected_lesson_ids != [lesson.id]:
                raise ValueError("Lesson Writer must return exactly its assigned lesson")
            _canonical_sources(
                f"lesson_writer:{lesson.id}", artifact.source_refs, [item.model_dump(mode="json") for item in course_plan.source_refs]
            )
            drafts.extend(artifact.lessons)
            sources.update({item.id: item for item in artifact.source_refs})
            objective_ids.update(artifact.objective_ids)
            competency_ids.update(artifact.competency_ids)

        return WriterArtifact(
            source_refs=list(sources.values()),
            expected_lesson_ids=[item.id for item in course_plan.lessons],
            objective_ids=sorted(objective_ids),
            competency_ids=sorted(competency_ids),
            lessons=drafts,
        )

    def _create_assessments(
        self,
        *,
        common: dict,
        settings_snapshot: dict,
        course_plan: CoursePlan,
        writer: WriterArtifact,
        qa_feedback: QAArtifact | None,
        revision: int,
    ) -> AssessmentArtifact:
        assessment = self.runtime.execute(
            agent="assessment",
            artifact="assessment_set",
            sequence=revision,
            template_name="assessment_agent_prompt.j2",
            response_model=AssessmentArtifact,
            prompt_context={
                **common,
                "course_plan_json": _json(course_plan),
                "writer_artifact_json": _json(writer),
                "module_tests_enabled": settings_snapshot["module_tests_enabled"],
                "final_test_enabled": settings_snapshot["final_test_enabled"],
                "assessment_settings_json": _json(
                    {
                        "module_tests_enabled": settings_snapshot["module_tests_enabled"],
                        "final_test_enabled": settings_snapshot["final_test_enabled"],
                    }
                ),
                "source_catalog_json": _json(course_plan.source_refs),
                "qa_feedback_json": _json(qa_feedback) if qa_feedback else "null",
            },
            max_tokens=4096,
        )
        _canonical_sources(
            "assessment",
            assessment.source_refs,
            [item.model_dump(mode="json") for item in course_plan.source_refs],
        )
        if not assessment.practices or not assessment.cases or not assessment.rubrics:
            raise ValueError(
                "Assessment Agent must generate practices, cases, and their rubrics"
            )
        if settings_snapshot["module_tests_enabled"]:
            tested_modules = {
                item.target_id for item in assessment.questions if item.scope == "module"
            }
            missing = {item.id for item in course_plan.modules} - tested_modules
            if missing:
                raise ValueError(f"Assessment Agent omitted module tests: {sorted(missing)}")
        if settings_snapshot["final_test_enabled"] and not any(
            item.scope == "final" for item in assessment.questions
        ):
            raise ValueError("Assessment Agent omitted the final test")
        return assessment

    def _review(
        self,
        *,
        common: dict,
        ingestion: IngestionArtifact,
        competency_map: CompetencyMapArtifact,
        course_plan: CoursePlan,
        writer: WriterArtifact,
        assessment: AssessmentArtifact,
        revision: int,
    ) -> QAArtifact:
        qa = self.runtime.execute(
            agent="critic_qa",
            artifact="qa_report",
            sequence=revision,
            template_name="critic_qa_prompt.j2",
            response_model=QAArtifact,
            prompt_context={
                **common,
                "ingestion_artifact_json": _json(ingestion),
                "competency_map_json": _json(competency_map),
                "course_plan_json": _json(course_plan),
                "writer_artifact_json": _json(writer),
                "assessment_artifact_json": _json(assessment),
                "candidate_artifacts_json": _json(
                    {
                        "ingestion": ingestion,
                        "competency_map": competency_map,
                        "course_plan": course_plan,
                        "writer": writer,
                        "assessment": assessment,
                    }
                ),
                "source_catalog_json": _json(ingestion.source_refs),
            },
            max_tokens=4096,
        )
        _canonical_sources(
            "critic_qa",
            qa.source_refs,
            [item.model_dump(mode="json") for item in ingestion.source_refs],
        )
        return qa

    @staticmethod
    def _to_graph(
        result: AgenticPipelineResult, settings_snapshot: dict
    ) -> tuple[list[dict], list[dict]]:
        plan = result.course_plan
        drafts = {item.id: item for item in result.writer.lessons}
        module_for_lesson = {
            lesson_id: module.id
            for module in plan.modules
            for lesson_id in module.lesson_ids
        }
        rubrics = {
            item.id: item.model_dump(mode="json") for item in result.assessment.rubrics
        }
        practices_by_lesson: dict[str, list[dict]] = {}
        cases_by_lesson: dict[str, list[dict]] = {}
        for practice in result.assessment.practices:
            payload = practice.model_dump(mode="json")
            payload["rubric"] = rubrics[practice.rubric_id]
            practices_by_lesson.setdefault(practice.lesson_id, []).append(payload)
        for case in result.assessment.cases:
            payload = case.model_dump(mode="json")
            payload["rubric"] = rubrics[case.rubric_id]
            cases_by_lesson.setdefault(case.lesson_ids[0], []).append(payload)

        nodes: list[dict] = []
        edges: list[dict] = []
        for module in plan.modules:
            nodes.append(
                {
                    "id": module.id,
                    "label": module.title,
                    "description": module.description,
                    "type": "module",
                    "objective_ids": module.objective_ids,
                    "competency_ids": module.competency_ids,
                    "source_refs": module.source_ref_ids,
                }
            )
        for lesson in plan.lessons:
            draft = drafts[lesson.id]
            content = "\n\n".join(
                f"## {section.heading}\n\n{section.content_markdown}"
                for section in draft.sections
            )
            assessment_refs = {
                ref_id
                for item in [
                    *practices_by_lesson.get(lesson.id, []),
                    *cases_by_lesson.get(lesson.id, []),
                ]
                for ref_id in [
                    *item.get("source_ref_ids", []),
                    *(item.get("rubric") or {}).get("source_ref_ids", []),
                ]
            }
            nodes.append(
                {
                    "id": lesson.id,
                    "label": draft.title,
                    "description": draft.summary,
                    "content": content,
                    "type": "lesson",
                    "objective_ids": draft.objective_ids,
                    "competency_ids": draft.competency_ids,
                    "source_refs": sorted(set(draft.source_ref_ids) | assessment_refs),
                    "practices": practices_by_lesson.get(lesson.id, []),
                    "cases": cases_by_lesson.get(lesson.id, []),
                }
            )

        for module in plan.modules:
            for lesson_id in module.lesson_ids:
                edges.append(
                    {"source": module.id, "target": lesson_id, "relation": "contains"}
                )
            for previous, current in zip(module.lesson_ids, module.lesson_ids[1:]):
                edges.append(
                    {"source": previous, "target": current, "relation": "precedes"}
                )
        for previous, current in zip(plan.modules, plan.modules[1:]):
            edges.append(
                {"source": previous.id, "target": current.id, "relation": "precedes"}
            )
        for module in plan.modules:
            for prerequisite in module.prerequisite_module_ids:
                edges.append(
                    {"source": prerequisite, "target": module.id, "relation": "requires"}
                )
        for lesson in plan.lessons:
            for prerequisite in lesson.prerequisite_lesson_ids:
                edges.append(
                    {"source": prerequisite, "target": lesson.id, "relation": "requires"}
                )

        questions_by_target: dict[tuple[str, str], list] = {}
        for question in result.assessment.questions:
            if question.scope == "final":
                if not settings_snapshot["final_test_enabled"]:
                    continue
                key = ("final", plan.id)
            else:
                if not settings_snapshot["module_tests_enabled"]:
                    continue
                module_id = (
                    question.target_id
                    if question.scope == "module"
                    else module_for_lesson[question.target_id]
                )
                key = ("module", module_id)
            questions_by_target.setdefault(key, []).append(question)

        for (scope, target_id), questions in questions_by_target.items():
            test_id = f"test:{scope}:{target_id}"
            question_payloads = []
            source_refs: set[str] = set()
            for question in questions:
                option_by_id = {item.id: item.text for item in question.options}
                correct = (
                    " | ".join(option_by_id[item] for item in question.correct_option_ids)
                    if question.correct_option_ids
                    else question.expected_answer or ""
                )
                question_payloads.append(
                    {
                        "id": question.id,
                        "question": question.prompt,
                        "answers": [item.text for item in question.options],
                        "correct_answer": correct,
                        "explanation": question.explanation,
                        "objective_ids": question.objective_ids,
                        "competency_ids": question.competency_ids,
                        "source_refs": question.source_ref_ids,
                    }
                )
                source_refs.update(question.source_ref_ids)
            nodes.append(
                {
                    "id": test_id,
                    "label": "Итоговый тест" if scope == "final" else "Тест модуля",
                    "type": "test",
                    "assessment_scope": scope,
                    "questions": question_payloads,
                    "source_refs": sorted(source_refs),
                }
            )
            if scope == "module":
                edges.append(
                    {"source": target_id, "target": test_id, "relation": "contains"}
                )
            else:
                for module in plan.modules:
                    edges.append(
                        {"source": module.id, "target": test_id, "relation": "precedes"}
                    )
        return nodes, edges
