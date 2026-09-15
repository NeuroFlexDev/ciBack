from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from fastapi import HTTPException

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
    from app.ai.evidence import compact_artifact
    value = compact_artifact(value)
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

    def run(self, *, course_title: str, settings_snapshot: dict,
            source_catalog: list[dict]) -> AgenticGraphBuild:
        """A durable graph of specialized, typed LangChain agents with repair routing."""
        from langgraph.graph import StateGraph, START, END
        from app.ai.checkpoints import checkpoint_store
        from app.ai.course_state import CourseAgentState
        import hashlib

        self.runtime.register_sources(source_catalog)
        common = {
            "course_title": course_title, "goal": settings_snapshot["goal"],
            "target_audience": settings_snapshot.get("target_audience") or "not specified",
            "difficulty": settings_snapshot["difficulty"], "language": settings_snapshot["language"],
            "lesson_count": settings_snapshot["lesson_count"],
        }
        def ingest(state):
            self.checkpoint("ingestion", 5)
            try:
                result = self._ingest(common, source_catalog, state.get("qa"))
                return {"ingestion": result.model_dump(mode="json")}
            except LegacyGraphResponse as exc:
                return {"legacy": exc.payload}

        def competencies(state):
            from app.ai.curriculum import complete_competency_inventories
            self.checkpoint("competency_mapping", 20)
            def validate_competencies(value):
                _canonical_sources("competency_mapper", value.source_refs, source_catalog)
                known = {item["id"] for item in state["ingestion"]["knowledge_items"]}
                unknown = set(value.source_knowledge_item_ids) - known
                if unknown:
                    raise ValueError(f"Competency map cites unknown ingestion knowledge IDs: {sorted(unknown)}")
            result = self.runtime.execute(agent="competency_mapper", artifact="competency_map",
                sequence=state["revision"], template_name="competency_mapper_prompt.j2",
                response_model=CompetencyMapArtifact, max_tokens=10000,
                prompt_context={**common, "ingestion_artifact_json": _json(state["ingestion"]),
                                "qa_feedback_json": _json(state.get("qa"))},
                validator=validate_competencies,
                normalize_response=lambda raw: complete_competency_inventories(raw,
                    sources=set(self.runtime.source_catalog),
                    knowledge={item["id"] for item in state["ingestion"]["knowledge_items"]}))
            return {"competency_map": result.model_dump(mode="json")}

        def architect(state):
            self.checkpoint("course_architecture", 35)
            def validate(value):
                _canonical_sources("architect", value.source_refs, source_catalog)
                if len(value.lessons) != settings_snapshot["lesson_count"]:
                    raise ValueError("Course Architect returned an unexpected lesson count")
                known = {x["id"] for x in state["competency_map"]["competencies"]}
                if set(value.competency_ids) != known:
                    raise ValueError("Course plan must cover every mapped competency")
            result = self.runtime.execute(agent="course_architect", artifact="course_plan",
                sequence=state["revision"], template_name="course_architect_prompt.j2",
                response_model=CoursePlan, max_tokens=12000,
                prompt_context={**common, "competency_map_json": _json(state["competency_map"]),
                    "source_catalog_json": _json([{"id": x["id"], "section": x.get("section")} for x in source_catalog]),
                    "qa_feedback_json": _json(state.get("qa"))}, validator=validate)
            return {"course_plan": result.model_dump(mode="json")}

        def write(state):
            self.checkpoint("lesson_writing", 50)
            result = self._write_lessons(common=common,
                course_plan=CoursePlan.model_validate(state["course_plan"]),
                qa_feedback=QAArtifact.model_validate(state["qa"]) if state.get("qa") else None,
                revision=state["revision"])
            return {"writer": result.model_dump(mode="json")}

        def assess(state):
            self.checkpoint("assessment_generation", 72)
            result = self._create_assessments(common=common, settings_snapshot=settings_snapshot,
                course_plan=CoursePlan.model_validate(state["course_plan"]),
                writer=WriterArtifact.model_validate(state["writer"]),
                qa_feedback=QAArtifact.model_validate(state["qa"]) if state.get("qa") else None,
                revision=state["revision"])
            return {"assessment": result.model_dump(mode="json")}

        def review(state):
            self.checkpoint("quality_assurance", 88)
            result = self._review(common=common,
                ingestion=IngestionArtifact.model_validate(state["ingestion"]),
                competency_map=CompetencyMapArtifact.model_validate(state["competency_map"]),
                course_plan=CoursePlan.model_validate(state["course_plan"]),
                writer=WriterArtifact.model_validate(state["writer"]),
                assessment=AssessmentArtifact.model_validate(state["assessment"]),
                revision=state["revision"])
            # Structural consistency is part of the gate, never left to model self-assessment.
            AgenticPipelineResult.model_validate({k: state[k] for k in (
                "ingestion", "competency_map", "course_plan", "writer", "assessment") } | {"qa": result})
            return {"qa": result.model_dump(mode="json")}

        def route_review(state):
            qa = QAArtifact.model_validate(state["qa"])
            if qa.verdict == "pass":
                return END
            if state["revision"] >= settings.AI_MAX_REVISIONS:
                raise ValueError("QA rejected the course after the revision limit")
            return "repair"

        def repair(state):
            types = {issue["artifact_type"] for issue in state["qa"]["issues"] if issue["severity"] in {"error", "blocker"}}
            target = ("ingestion" if "ingestion" in types else "competency_mapping" if "competency_map" in types
                      else "course_architecture" if "course_plan" in types else "lesson_writing" if "lesson" in types
                      else "assessment_generation")
            return {"revision": state["revision"] + 1, "repair_target": target}

        builder = StateGraph(CourseAgentState)
        for name, fn in [("ingestion", ingest), ("competency_mapping", competencies),
                         ("course_architecture", architect), ("lesson_writing", write),
                         ("assessment_generation", assess), ("quality_assurance", review), ("repair", repair)]:
            builder.add_node(name, fn)
        builder.add_edge(START, "ingestion")
        builder.add_conditional_edges("ingestion", lambda s: END if s.get("legacy") else "competency_mapping")
        for before, after in [("competency_mapping", "course_architecture"), ("course_architecture", "lesson_writing"),
                              ("lesson_writing", "assessment_generation"), ("assessment_generation", "quality_assurance")]:
            builder.add_edge(before, after)
        builder.add_conditional_edges("quality_assurance", route_review)
        builder.add_conditional_edges("repair", lambda s: s["repair_target"])
        from app.models.generation_run import GenerationRun
        run = self.runtime.db.get(GenerationRun, self.runtime.run_id)
        from app.ai.version import runtime_signature
        identity = _json([runtime_signature(), common, settings_snapshot, source_catalog]) + f"{settings.DATABASE_URL}:{self.runtime.owner_id}:{self.runtime.course_id}:{self.runtime.run_id}:{run.created_at}"
        thread_id = hashlib.sha256(identity.encode()).hexdigest()
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 80}
        with checkpoint_store() as saver:
            graph = builder.compile(checkpointer=saver)
            previous = graph.get_state(config)
            initial = None if previous.values else {"revision": 0}
            if previous.values and not previous.next:
                final = previous.values
            else:
                final = graph.invoke(initial, config, durability="sync")
        if final.get("legacy"):
            nodes, edges = GeneratedGraphPayload.model_validate(final["legacy"]).json_payload()
            return AgenticGraphBuild(nodes, edges, None, True)
        result = AgenticPipelineResult.model_validate({k: final[k] for k in (
            "ingestion", "competency_map", "course_plan", "writer", "assessment", "qa")})
        nodes, edges = self._to_graph(result, settings_snapshot)
        self.checkpoint("materialization", 96)
        return AgenticGraphBuild(nodes, edges, result)

    def _ingest(self, common: dict, catalog: list[dict], feedback=None) -> IngestionArtifact:
        from app.ai.evidence import source_batches, merge_ingestion
        artifacts = []
        batches = source_batches(catalog, settings.AI_INGESTION_BATCH_CHARS)
        for index, batch in enumerate(batches):
            artifact = self.runtime.execute(agent="ingestion", artifact="document_knowledge",
                sequence=index, template_name="ingestion_agent_prompt.j2", response_model=IngestionArtifact,
                prompt_context={**common, "source_catalog_json": _json(batch),
                                "qa_feedback_json": _json(feedback)}, max_tokens=6000,
                allow_legacy_graph=settings.ENV == "test",
                validator=lambda value, batch=batch: _canonical_sources("ingestion", value.source_refs, batch, require_all=True))
            artifacts.append(artifact)
        return merge_ingestion(artifacts)

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
            from app.ai.evidence import rank_sources
            extra_sources = rank_sources(lesson.title + " " + lesson.description + " " + " ".join(o.text for o in objectives),
                                         list(self.runtime.source_catalog.values()), limit=4)
            lesson_sources = list({item.id: item for item in [*lesson_sources, *map(SourceRef.model_validate, extra_sources)]}.values())
            def validate_lesson(value):
                from app.ai.evidence import learner_markdown
                if value.expected_lesson_ids != [lesson.id]:
                    raise ValueError("Writer must return exactly the assigned lesson ID")
                draft = value.lessons[0]
                for section in draft.sections:
                    learner_markdown(section.content_markdown, set(section.source_ref_ids))
                if set(draft.objective_ids) != set(lesson.objective_ids) or set(draft.competency_ids) != set(lesson.competency_ids):
                    raise ValueError("Writer must preserve the lesson's objective and competency IDs")
                _canonical_sources("writer", value.source_refs, [s.model_dump(mode="json") for s in lesson_sources])
            artifact = self.runtime.execute(
                agent="lesson_writer",
                artifact="lesson_draft",
                sequence=revision * 1000 + index,
                template_name="lesson_writer_prompt.j2",
                response_model=WriterArtifact,
                prompt_context={
                    **common,
                    "course_plan_json": _json({"id": course_plan.id, "title": course_plan.title, "goal": course_plan.goal,
                        "modules": [{"id": m.id, "title": m.title, "lesson_ids": m.lesson_ids} for m in course_plan.modules]}),
                    "lesson_spec_json": _json(lesson),
                    "lesson_objectives_json": _json(objectives),
                    "source_refs_json": _json(lesson_sources),
                    "lesson_ids_json": _json([lesson.id]),
                    "source_catalog_json": _json(lesson_sources),
                    "qa_feedback_json": _json({"issues": [issue for issue in qa_feedback.issues
                        if issue.artifact_type == "lesson" and issue.artifact_id == lesson.id]})
                        if qa_feedback and any(issue.artifact_type == "lesson" and issue.artifact_id == lesson.id
                                               for issue in qa_feedback.issues) else "null",
                },
                max_tokens=4096,
                validator=validate_lesson,
            )
            if artifact.expected_lesson_ids != [lesson.id]:
                raise ValueError("Lesson Writer must return exactly its assigned lesson")
            _canonical_sources(
                f"lesson_writer:{lesson.id}", artifact.source_refs, list(self.runtime.source_catalog.values())
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

    def _create_assessments(self, *, common, settings_snapshot, course_plan, writer, qa_feedback, revision):
        if len(course_plan.lessons) == 1:
            return self._assess_batch(common=common, settings_snapshot=settings_snapshot,
                course_plan=course_plan, writer=writer, qa_feedback=qa_feedback, revision=revision)
        from app.ai.curriculum import slice_curriculum, merge_assessments
        parts = []
        for offset in range(0, len(course_plan.lessons), 2):
            ids = {lesson.id for lesson in course_plan.lessons[offset:offset+2]}
            plan_part, writer_part = slice_curriculum(course_plan, writer, ids)
            try:
                parts.append(self._assess_batch(common=common, settings_snapshot=settings_snapshot,
                    course_plan=plan_part, writer=writer_part, qa_feedback=qa_feedback,
                    revision=revision*1000+offset))
            except HTTPException as exc:
                if exc.status_code != 413 or len(ids) < 2:
                    raise
                for step, lesson in enumerate(course_plan.lessons[offset:offset+2]):
                    part_plan, part_writer = slice_curriculum(course_plan, writer, {lesson.id})
                    parts.append(self._assess_batch(common=common, settings_snapshot=settings_snapshot,
                        course_plan=part_plan, writer=part_writer, qa_feedback=qa_feedback,
                        revision=revision*1000+offset+step))
        return merge_assessments(parts)

    def _assess_batch(
        self,
        *,
        common: dict,
        settings_snapshot: dict,
        course_plan: CoursePlan,
        writer: WriterArtifact,
        qa_feedback: QAArtifact | None,
        revision: int,
    ) -> AssessmentArtifact:
        def validate_assessment(value):
            _canonical_sources("assessment", value.source_refs, list(self.runtime.source_catalog.values()))
            if not value.practices or not value.cases or not value.rubrics:
                raise ValueError("Assessment must include practices, cases and rubrics")
            if set(value.module_ids) != {m.id for m in course_plan.modules} or set(value.lesson_ids) != {l.id for l in course_plan.lessons}:
                raise ValueError("Assessment must preserve all module and lesson IDs from course plan")
            if value.course_plan_id != course_plan.id or set(value.objective_ids) != {o.id for o in course_plan.objectives} or set(value.competency_ids) != set(course_plan.competency_ids):
                raise ValueError("Assessment must preserve the exact plan, objective and competency IDs")
            if settings_snapshot["module_tests_enabled"]:
                tested = {q.target_id for q in value.questions if q.scope == "module"}
                missing = {m.id for m in course_plan.modules} - tested
                if missing:
                    raise ValueError(f"Missing module tests: {sorted(missing)}. Use scope=module, target_id equal to the exact module ID")
            if settings_snapshot["final_test_enabled"] and not any(q.scope == "final" for q in value.questions):
                raise ValueError("Missing final test. Use scope=final and target_id equal to the exact course plan ID")
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
            validator=validate_assessment,
        )
        _canonical_sources(
            "assessment",
            assessment.source_refs,
            list(self.runtime.source_catalog.values()),
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

    def _review(self, *, common, ingestion, competency_map, course_plan, writer, assessment, revision):
        # Start with pairs; evidence-heavy pairs split further before any paid call.
        if len(writer.lessons) == 1:
            return self._review_batch(common=common, ingestion=ingestion, competency_map=competency_map,
                course_plan=course_plan, writer=writer, assessment=assessment, revision=revision)
        from app.ai.curriculum import slice_curriculum, assessment_excerpt
        reports = []
        for offset in range(0, len(writer.lessons), 2):
            lessons = writer.lessons[offset:offset+2]
            ids = {lesson.id for lesson in lessons}
            partial_plan, partial = slice_curriculum(course_plan, writer, ids)
            try:
                reports.append(self._review_batch(common=common, ingestion=ingestion, competency_map=competency_map,
                    course_plan=partial_plan, writer=partial, assessment=assessment_excerpt(assessment, partial_plan), revision=revision*1000+offset))
            except HTTPException as exc:
                if exc.status_code != 413 or len(lessons) < 2:
                    raise
                for step, lesson in enumerate(lessons):
                    part_plan, part_writer = slice_curriculum(course_plan, writer, {lesson.id})
                    reports.append(self._review_batch(common=common, ingestion=ingestion, competency_map=competency_map,
                        course_plan=part_plan, writer=part_writer, assessment=assessment_excerpt(assessment, part_plan),
                        revision=revision*1000+offset+step))
        payload = reports[0].model_dump(mode="json")
        payload["source_refs"] = list({ref.id: ref.model_dump(mode="json") for report in reports for ref in report.source_refs}.values())
        payload["checked_artifact_ids"] = sorted({ref for report in reports for ref in report.checked_artifact_ids})
        payload["issues"] = []
        payload["revision_required_for"] = []
        for index, report in enumerate(reports):
            for issue in report.issues:
                item = issue.model_dump(mode="json")
                item["id"] = f"issue:b{index}:" + item["id"][6:110]
                payload["issues"].append(item)
                if issue.id in report.revision_required_for:
                    payload["revision_required_for"].append(item["id"])
        payload["verdict"] = "fail" if any(r.verdict == "fail" for r in reports) else "revise" if any(r.verdict == "revise" for r in reports) else "pass"
        for field in ("coverage_score", "grounding_score", "difficulty_score", "assessment_quality_score"):
            payload[field] = min(getattr(report, field) for report in reports)
        payload["summary"] = "\n".join(report.summary for report in reports)[:4000]
        return QAArtifact.model_validate(payload)

    def _review_batch(
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
        known_ids = set()
        def collect_ids(value):
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            if isinstance(value, dict):
                if isinstance(value.get("id"), str): known_ids.add(value["id"])
                for item in value.values(): collect_ids(item)
            elif isinstance(value, list):
                for item in value: collect_ids(item)
        for artifact in (ingestion, competency_map, course_plan, writer, assessment): collect_ids(artifact)
        def validate_qa(value):
            claimed = set(value.checked_artifact_ids) | {issue.artifact_id for issue in value.issues if issue.artifact_id}
            if claimed - known_ids:
                raise ValueError(f"QA referenced unknown artifact IDs: {sorted(claimed - known_ids)}")
            _canonical_sources("critic_qa", value.source_refs, list(self.runtime.source_catalog.values()))
            missing = {lesson.id for lesson in writer.lessons} - set(value.checked_artifact_ids)
            if missing:
                raise ValueError(f"QA must check every lesson, missing IDs: {sorted(missing)}")
        from app.ai.curriculum import competency_excerpt
        ontology = competency_excerpt(competency_map, course_plan)
        candidate = {
            "review_scope": "partial course batch; other lessons are checked separately",
            "ingestion": {"knowledge_items": [item.model_dump(mode="json") for item in ingestion.knowledge_items
                if item.id in ontology["source_knowledge_item_ids"]]},
            "competency_map": ontology, "course_plan": course_plan.model_dump(mode="json"),
            "writer": writer.model_dump(mode="json"),
            "assessment": assessment.model_dump(mode="json") if hasattr(assessment, "model_dump") else assessment,
        }
        # Include every referenced quote in the batch, not only writer citations.
        cited = set()
        def collect_refs(value):
            if isinstance(value, dict):
                if str(value.get("id", "")).startswith("src:"): cited.add(value["id"])
                for key, item in value.items():
                    if key.endswith("source_ref_ids") and isinstance(item, list): cited.update(item)
                    collect_refs(item)
            elif isinstance(value, list):
                for item in value: collect_refs(item)
        collect_refs(candidate)
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
                "candidate_artifacts_json": _json(candidate),
                "source_catalog_json": _json([ref for ref in ingestion.source_refs if ref.id in cited]),
                "qa_policy_json": _json({"minimum_score": settings.AI_QA_MIN_SCORE, "scope": "Check only the supplied curriculum batch. Other lessons and assessments are reviewed separately."}),
            },
            max_tokens=4096,
            validator=validate_qa,
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
            from app.ai.evidence import learner_markdown
            content = "\n\n".join(
                f"## {section.heading}\n\n{learner_markdown(section.content_markdown, set(section.source_ref_ids))}"
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
