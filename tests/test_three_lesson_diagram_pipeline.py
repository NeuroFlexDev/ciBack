"""Local provider-boundary regression: real contracts, batching and persistence."""
import json
from collections import Counter

import pytest

from app.ai import gateway
from app.models.agent_artifact import AgentArtifact
from app.models.generation_run import GenerationRun
from app.models.learning import AssessmentDefinition
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.theory import Theory
from app.services.agent_runtime import AgentRuntime
from app.services.agentic_course_pipeline import AgenticCoursePipeline
from app.services.course_materialization_service import CourseMaterializationService
from app.services.generation_service import generate_from_prompt
from app.services.source_catalog_service import graph_source_links
from tests.factories import make_course


DIAGRAMS = [
    '```mermaid\nflowchart LR\n    A["Проверка"] --> B{"Безопасно?"}\n    B -->|Да| C["Работа"]\n```',
    '```mermaid\nsequenceDiagram\n    participant A as Оператор\n    participant B as Система\n    A->>B: Проверить состояние\n    B-->>A: Результат\n```',
    '```mermaid\nclassDiagram\n    class Checklist {\n        +verify() bool\n    }\n```',
]


def _fixtures():
    source = {"id": "src:doc:1:v1:chunk:1", "document_id": 1, "document_version": 1,
        "document_content_hash": "immutable-policy-hash", "chunk_id": 1, "chunk_index": 0,
        "page": 1, "section": "Проверка", "quote": "Оператор проверяет состояние системы перед работой и записывает результат."}
    evidence = {"source_ref_ids": [source["id"]]}
    aligned = {**evidence, "competency_ids": ["cmp:safety"]}
    base = {"artifact_version": "1.0", "source_refs": [source["id"]]}
    ingestion = {**base, "documents": [{"document_id": 1, "document_version": 1,
        "title": "Правила", "summary": source["quote"], **evidence, "knowledge_item_ids": ["kn:check"]}],
        "knowledge_items": [{"id": "kn:check", "kind": "requirement", "title": "Проверка",
            "statement": source["quote"], **evidence, "related_item_ids": [], "attributes": {}}], "warnings": []}
    competency = {**base, "source_knowledge_item_ids": ["kn:check"],
        "roles": [{"id": "role:operator", "title": "Оператор", "description": "Работает безопасно", **aligned}],
        "competencies": [{"id": "cmp:safety", "title": "Безопасность", "description": "Выполняет проверку",
            "level": "basic", "skill_ids": ["skill:check"], **evidence}],
        "skills": [{"id": "skill:check", "title": "Проверка", "description": "Проверяет состояние",
            "knowledge_ids": ["know:rule"], "procedure_ids": [], **evidence}],
        "knowledge": [{"id": "know:rule", "title": "Правило", "description": source["quote"],
            "source_knowledge_item_ids": ["kn:check"], **evidence}], "procedures": []}
    lessons = [{"id": f"lesson:check_{i}", "title": f"Проверка {i}", "description": "Порядок проверки",
        "estimated_minutes": 10, "objective_ids": [f"obj:check_{i}"], **aligned,
        "prerequisite_lesson_ids": [f"lesson:check_{i-1}"] if i else []} for i in range(3)]
    plan = {**base, "id": "plan:safety", "title": "Безопасная работа", "goal": "Выполнить проверку",
        "target_audience": "Операторы", "difficulty": "basic", "language": "ru", "estimated_minutes": 30,
        "competency_ids": ["cmp:safety"], "objectives": [{"id": f"obj:check_{i}", "text": f"Выполнить проверку {i}",
            "bloom_level": "apply", "measurable_verb": "выполнить", **aligned} for i in range(3)],
        "modules": [{"id": "mod:safety", "title": "Проверки", "description": "Три проверки",
            "lesson_ids": [l["id"] for l in lessons], "objective_ids": [f"obj:check_{i}" for i in range(3)],
            "prerequisite_module_ids": [], **aligned}], "lessons": lessons}
    return source, base, ingestion, competency, plan


def _assessment(base, plan):
    alignment = {"objective_ids": [o["id"] for o in plan["objectives"]],
        "competency_ids": plan["competency_ids"], "source_ref_ids": base["source_refs"]}
    rubrics, practices, cases = [], [], []
    for lesson in plan["lessons"]:
        suffix = lesson["id"].split(":")[1]
        local = {**alignment, "objective_ids": lesson["objective_ids"]}
        for kind in ("practice", "case"):
            rubrics.append({"id": f"rubric:{kind}_{suffix}", "title": "Корректность проверки", **local,
                "criteria": [{"id": f"criterion:{kind}_{suffix}", "title": "Порядок действий", "description": "Проверка до работы",
                    "weight": 1.0, "levels": [{"id": "level:missing", "title": "Нет", "description": "Проверка пропущена", "score": 0},
                        {"id": "level:complete", "title": "Да", "description": "Проверка выполнена", "score": 2}]}], "passing_score": 1})
        practices.append({"id": f"practice:{suffix}", "lesson_id": lesson["id"], "title": "Проверьте состояние",
            "instructions": "Выполните проверку до работы.", "deliverable": "Запись результата",
            "rubric_id": f"rubric:practice_{suffix}", **local})
        cases.append({"id": f"case:{suffix}", "title": "Проверка пропущена", "scenario": "Оператор начал работу сразу.",
            "prompts": ["Как исправить ситуацию?"], "expected_response": "Остановиться и проверить состояние.",
            "lesson_ids": [lesson["id"]], "rubric_id": f"rubric:case_{suffix}", **local})
    questions = []
    for scope, target in (("module", "mod:safety"), ("final", "plan:safety")):
        questions.append({"id": f"question:{scope}", "kind": "multiple_choice", "scope": scope, "target_id": target,
            "prompt": "Какие действия обязательны?", "options": [{"id": f"option:{scope}_check", "text": "Проверить состояние"},
                {"id": f"option:{scope}_record", "text": "Записать результат"}, {"id": f"option:{scope}_skip", "text": "Пропустить проверку"}],
            "correct_option_ids": [f"option:{scope}_check", f"option:{scope}_record"], "expected_answer": None,
            "explanation": "Оба действия предусмотрены правилами.", **alignment})
    return {**base, "course_plan_id": plan["id"], "module_ids": [m["id"] for m in plan["modules"]],
        "lesson_ids": [l["id"] for l in plan["lessons"]], "objective_ids": alignment["objective_ids"],
        "competency_ids": alignment["competency_ids"], "questions": questions, "practices": practices,
        "cases": cases, "rubrics": rubrics}


@pytest.mark.parametrize("envelope", [False, True], ids=["raw-json", "outer-json-fence"])
def test_three_mermaid_lessons_reach_assessment_qa_and_persistence(db_session, auth_user, monkeypatch, envelope):
    source, base, ingestion, competency, plan = _fixtures()
    course = make_course(db_session, owner_id=auth_user.id)
    run = GenerationRun(owner_id=auth_user.id, course_id=course.id, run_type="graph_generation", status="running", input_docs=[])
    db_session.add(run)
    db_session.commit()
    calls, provider_calls, batches = [], [], {"assessment": [], "qa": []}
    reply = {}

    def provider(*args, **kwargs):
        # The only provider boundary is replaced: actual prompt rendering, outer
        # JSON parsing, immutable source hydration and typed runtime stay real.
        provider_calls.append(args)
        raw = json.dumps(reply["payload"], ensure_ascii=False)
        if envelope:
            raw = "```json\n" + raw + "\n```"
        return {"text": raw, "usage": {"input_tokens": 100, "output_tokens": 200},
            "usage_reported": True, "model": "local-fixture", "provider": "vsellm"}

    monkeypatch.setattr(gateway, "invoke_messages", provider)

    def generate(template_name, **kwargs):
        calls.append(template_name)
        if template_name == "ingestion_agent_prompt.j2":
            payload = ingestion
        elif template_name == "competency_mapper_prompt.j2":
            payload = competency
        elif template_name == "course_architect_prompt.j2":
            payload = plan
        elif template_name == "lesson_writer_prompt.j2":
            lesson = json.loads(kwargs["lesson_spec_json"])
            index = int(lesson["id"].rsplit("_", 1)[1])
            alignment = {k: lesson[k] for k in ("objective_ids", "competency_ids", "source_ref_ids")}
            payload = {**base, "expected_lesson_ids": [lesson["id"]], "objective_ids": lesson["objective_ids"],
                "competency_ids": lesson["competency_ids"], "lessons": [{"id": lesson["id"], "title": lesson["title"],
                    "summary": "Порядок действий", **alignment, "sections": [{"id": f"section:check_{index}",
                        "heading": "Схема проверки", "content_markdown": DIAGRAMS[index] + '\n\nФормула: $x^2 + y^2$.\n\n```json\n{"status": "checked"}\n```',
                        "source_ref_ids": [source["id"]]}], "key_takeaways": ["Проверяйте до работы."]}]}
        elif template_name == "assessment_agent_prompt.j2":
            partial = json.loads(kwargs["course_plan_json"])
            batches["assessment"].append([l["id"] for l in partial["lessons"]])
            payload = _assessment(base, partial)
        elif template_name == "critic_qa_prompt.j2":
            partial = json.loads(kwargs["writer_artifact_json"])
            ids = [l["id"] for l in partial["lessons"]]
            batches["qa"].append(ids)
            assert all("```mermaid" in l["sections"][0]["content_markdown"] for l in partial["lessons"])
            payload = {**base, "checked_artifact_ids": [plan["id"], *ids], "issues": [], "verdict": "pass",
                "coverage_score": 1, "grounding_score": 1, "difficulty_score": 1, "assessment_quality_score": 1,
                "revision_required_for": [], "summary": "Схемы и задания согласованы с источником."}
        else:
            pytest.fail(f"Unexpected template: {template_name}")
        reply["payload"] = payload
        kwargs.update(engine="vsellm", include_external_context=False, use_feedback=False)
        return generate_from_prompt(template_name, **kwargs)

    snapshot = {"goal": plan["goal"], "target_audience": plan["target_audience"], "difficulty": "basic", "language": "ru",
        "lesson_count": 3, "module_tests_enabled": True, "final_test_enabled": True, "web_search_enabled": False}
    runtime = AgentRuntime(db=db_session, run_id=run.id, course_id=course.id, generate=generate)
    checkpoints = []
    build = AgenticCoursePipeline(runtime=runtime, checkpoint=lambda stage, progress: checkpoints.append(stage)).run(
        course_title=plan["title"], settings_snapshot=snapshot, source_catalog=[source])
    assert Counter(calls) == Counter({"ingestion_agent_prompt.j2": 1, "competency_mapper_prompt.j2": 1,
        "course_architect_prompt.j2": 1, "lesson_writer_prompt.j2": 3, "assessment_agent_prompt.j2": 2, "critic_qa_prompt.j2": 2})
    assert batches == {stage: [["lesson:check_0", "lesson:check_1"], ["lesson:check_2"]] for stage in ("assessment", "qa")}
    assert len(provider_calls) == 10
    assert build.qa_summary["verdict"] == "pass"
    materialized = CourseMaterializationService.materialize(db_session, course=course, nodes=build.nodes, edges=build.edges)
    db_session.commit()
    assert db_session.query(Lesson).filter_by(is_deleted=False).count() == 3
    theories = db_session.query(Theory).order_by(Theory.id).all()
    for diagram, theory in zip(DIAGRAMS, theories, strict=True):
        assert diagram in theory.content
        assert '```json\n{"status": "checked"}\n```' in theory.content
        assert "$x^2 + y^2$" in theory.content
    definitions = db_session.query(AssessmentDefinition).all()
    closed = [d for d in definitions if d.test_id]
    assert len(closed) == 4
    assert all(d.question_type == "multiple" and d.correct_answers == ["Проверить состояние", "Записать результат"] for d in closed)
    assert len([d for d in definitions if d.task_id]) == 6
    assert all(d.max_points == 2 and d.criteria for d in definitions if d.task_id)
    assert all("Остановиться и проверить состояние." not in task.description for task in db_session.query(Task).all())
    links = graph_source_links(materialized["canvas_nodes"], [source])
    assert {link["node_id"] for link in links} == {node["id"] for node in materialized["canvas_nodes"]}
    assert all(link["ref_id"] == source["id"] and link["excerpt"] == source["quote"] for link in links)
    # Resuming a fully checkpointed pipeline reuses every typed stage. A fresh
    # runtime must not call the provider, even for the final one-lesson batch.
    def no_more_calls(*args, **kwargs):
        pytest.fail("Completed stage was dispatched again")
    resumed = AgenticCoursePipeline(runtime=AgentRuntime(db=db_session, run_id=run.id, course_id=course.id, generate=no_more_calls),
        checkpoint=lambda *_: None).run(
        course_title=plan["title"], settings_snapshot=snapshot, source_catalog=[source])
    assert resumed.nodes == build.nodes
    assert db_session.query(AgentArtifact).filter(AgentArtifact.agent != "web_search").count() == 10
