from pydantic import BaseModel

from app.models.agent_artifact import AgentArtifact
from app.models.document import Document, DocumentChunk
from app.models.course_graph import CourseGraph
from app.models.course_source_link import CourseSourceLink
from app.models.course_update_proposal import CourseUpdateProposal
from app.models.generation_run import GenerationRun
from app.models.user import User
from app.core.security import hash_password
from app.services.agent_runtime import AgentRuntime
from app.services.agentic_course_pipeline import AgenticCoursePipeline
from app.services.course_update_service import CourseUpdateService
from app.services.source_catalog_service import (
    build_source_catalog,
    graph_source_links,
)
from tests.factories import make_course


class ExampleArtifact(BaseModel):
    value: int


def test_agent_runtime_persists_and_reuses_typed_artifact(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    run = GenerationRun(
        owner_id=auth_user.id,
        course_id=course.id,
        run_type="graph_generation",
        status="running",
        input_docs=[],
    )
    db_session.add(run)
    db_session.commit()
    calls = []

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"value": 7, "_model": "scripted"}

    runtime = AgentRuntime(
        db=db_session,
        run_id=run.id,
        course_id=course.id,
        generate=generate,
    )
    first = runtime.execute(
        agent="ingestion",
        artifact="example",
        sequence=0,
        template_name="unused.j2",
        response_model=ExampleArtifact,
        prompt_context={"input": "same"},
        max_tokens=100,
    )
    second = runtime.execute(
        agent="ingestion",
        artifact="example",
        sequence=0,
        template_name="unused.j2",
        response_model=ExampleArtifact,
        prompt_context={"input": "same"},
        max_tokens=100,
    )

    assert first == second == ExampleArtifact(value=7)
    assert len(calls) == 1
    stored = db_session.query(AgentArtifact).one()
    assert (stored.status, stored.payload, stored.model) == (
        "completed",
        {"value": 7},
        "scripted",
    )


def test_source_catalog_is_round_robin_and_graph_refs_are_durable(
    db_session, auth_user
):
    course = make_course(db_session, owner_id=auth_user.id)
    documents = []
    for number in (1, 2):
        document = Document(
            storage_key=f"{number}.txt",
            owner_id=auth_user.id,
            course_id=course.id,
            version=1,
            status="indexed",
            content_hash=f"hash-{number}",
            source_type="upload",
            original_filename=f"{number}.txt",
            mime_type="text/plain",
            size_bytes=10,
        )
        db_session.add(document)
        db_session.flush()
        for chunk_index in (0, 1):
            db_session.add(
                DocumentChunk(
                    document_id=document.id,
                    document_version=1,
                    text=f"document {number} chunk {chunk_index}",
                    chunk_index=chunk_index,
                    metadata_json={},
                )
            )
        documents.append(document)
    db_session.commit()

    catalog = build_source_catalog(documents, max_chars=10_000)

    assert [item["document_id"] for item in catalog] == [
        documents[0].id,
        documents[1].id,
        documents[0].id,
        documents[1].id,
    ]
    links = graph_source_links(
        [
            {
                "id": "lesson:one",
                "type": "lesson",
                "source_refs": [catalog[0]["id"]],
            }
        ],
        catalog,
    )
    assert links[0]["ref_id"] == catalog[0]["id"]
    assert links[0]["excerpt"] == catalog[0]["quote"]
    assert len(links[0]["excerpt_hash"]) == 64


def test_agentic_pipeline_runs_all_typed_stages_and_passes_qa(
    db_session, auth_user
):
    course = make_course(db_session, owner_id=auth_user.id)
    run = GenerationRun(
        owner_id=auth_user.id,
        course_id=course.id,
        run_type="graph_generation",
        status="running",
        input_docs=[],
    )
    db_session.add(run)
    db_session.commit()
    source = {
        "id": "src:doc:1:v1:chunk:1",
        "document_id": 1,
        "document_version": 1,
        "document_content_hash": "hash-1",
        "chunk_id": 1,
        "chunk_index": 0,
        "page": None,
        "section": "Policy",
        "quote": "Operators must perform the safety check.",
    }
    ingestion = {
        "artifact_version": "1.0",
        "source_refs": [source],
        "documents": [
            {
                "document_id": 1,
                "document_version": 1,
                "title": "Policy",
                "summary": "Safety policy",
                "source_ref_ids": [source["id"]],
                "knowledge_item_ids": ["kn:safety_policy"],
            }
        ],
        "knowledge_items": [
            {
                "id": "kn:safety_policy",
                "kind": "requirement",
                "title": "Safety check",
                "statement": "Perform the safety check.",
                "source_ref_ids": [source["id"]],
                "related_item_ids": [],
                "attributes": {},
            }
        ],
        "warnings": [],
    }
    competency_map = {
        "artifact_version": "1.0",
        "source_refs": [source],
        "source_knowledge_item_ids": ["kn:safety_policy"],
        "roles": [
            {
                "id": "role:operator",
                "title": "Operator",
                "description": "Operates safely",
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": [source["id"]],
            }
        ],
        "competencies": [
            {
                "id": "cmp:safety",
                "title": "Safety",
                "description": "Apply safety checks",
                "level": "basic",
                "skill_ids": ["skill:safety_check"],
                "source_ref_ids": [source["id"]],
            }
        ],
        "skills": [
            {
                "id": "skill:safety_check",
                "title": "Check safety",
                "description": "Perform a check",
                "knowledge_ids": ["know:safety_rule"],
                "procedure_ids": [],
                "source_ref_ids": [source["id"]],
            }
        ],
        "knowledge": [
            {
                "id": "know:safety_rule",
                "title": "Safety rule",
                "description": "The required check",
                "source_knowledge_item_ids": ["kn:safety_policy"],
                "source_ref_ids": [source["id"]],
            }
        ],
        "procedures": [],
    }
    plan = {
        "artifact_version": "1.0",
        "id": "plan:safety",
        "title": "Safety",
        "goal": "Apply the safety check",
        "target_audience": "Operators",
        "difficulty": "basic",
        "language": "en",
        "estimated_minutes": 30,
        "source_refs": [source],
        "competency_ids": ["cmp:safety"],
        "objectives": [
            {
                "id": "obj:apply_check",
                "text": "Apply the safety check",
                "bloom_level": "apply",
                "measurable_verb": "apply",
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": [source["id"]],
            }
        ],
        "modules": [
            {
                "id": "mod:safety",
                "title": "Safety",
                "description": "Safety module",
                "lesson_ids": ["lesson:safety_check"],
                "objective_ids": ["obj:apply_check"],
                "competency_ids": ["cmp:safety"],
                "prerequisite_module_ids": [],
                "source_ref_ids": [source["id"]],
            }
        ],
        "lessons": [
            {
                "id": "lesson:safety_check",
                "title": "Safety check",
                "description": "Learn the check",
                "estimated_minutes": 30,
                "objective_ids": ["obj:apply_check"],
                "competency_ids": ["cmp:safety"],
                "prerequisite_lesson_ids": [],
                "source_ref_ids": [source["id"]],
            }
        ],
    }
    writer = {
        "artifact_version": "1.0",
        "source_refs": [source],
        "expected_lesson_ids": ["lesson:safety_check"],
        "objective_ids": ["obj:apply_check"],
        "competency_ids": ["cmp:safety"],
        "lessons": [
            {
                "id": "lesson:safety_check",
                "title": "Safety check",
                "summary": "Apply the required check",
                "objective_ids": ["obj:apply_check"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": [source["id"]],
                "sections": [
                    {
                        "id": "section:safety_check",
                        "heading": "Procedure",
                        "content_markdown": "Perform the safety check.",
                        "source_ref_ids": [source["id"]],
                    }
                ],
                "key_takeaways": ["Perform the check"],
            }
        ],
    }
    rubric = lambda rubric_id, criterion_id: {
        "id": rubric_id,
        "title": "Safety rubric",
        "objective_ids": ["obj:apply_check"],
        "competency_ids": ["cmp:safety"],
        "source_ref_ids": [source["id"]],
        "criteria": [
            {
                "id": criterion_id,
                "title": "Correctness",
                "description": "Correct execution",
                "weight": 1.0,
                "levels": [
                    {"id": "level:not_met", "title": "Not met", "description": "Incorrect", "score": 0},
                    {"id": "level:met", "title": "Met", "description": "Correct", "score": 1},
                ],
            }
        ],
        "passing_score": 1,
    }
    question = lambda question_id, scope, target_id: {
        "id": question_id,
        "kind": "single_choice",
        "scope": scope,
        "target_id": target_id,
        "prompt": "What is required?",
        "options": [
            {"id": f"option:{scope}_check", "text": "Perform the check"},
            {"id": f"option:{scope}_skip", "text": "Skip it"},
        ],
        "correct_option_ids": [f"option:{scope}_check"],
        "expected_answer": None,
        "explanation": "The policy requires the check.",
        "objective_ids": ["obj:apply_check"],
        "competency_ids": ["cmp:safety"],
        "source_ref_ids": [source["id"]],
    }
    assessment = {
        "artifact_version": "1.0",
        "course_plan_id": "plan:safety",
        "source_refs": [source],
        "module_ids": ["mod:safety"],
        "lesson_ids": ["lesson:safety_check"],
        "objective_ids": ["obj:apply_check"],
        "competency_ids": ["cmp:safety"],
        "questions": [
            question("question:module_check", "module", "mod:safety"),
            question("question:final_check", "final", "plan:safety"),
        ],
        "practices": [
            {
                "id": "practice:safety_check",
                "lesson_id": "lesson:safety_check",
                "title": "Perform a check",
                "instructions": "Run the check",
                "deliverable": "Checklist",
                "rubric_id": "rubric:practice_check",
                "objective_ids": ["obj:apply_check"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": [source["id"]],
            }
        ],
        "cases": [
            {
                "id": "case:skipped_check",
                "title": "Skipped check",
                "scenario": "A check was skipped.",
                "prompts": ["What should happen?"],
                "expected_response": "Perform the check.",
                "lesson_ids": ["lesson:safety_check"],
                "rubric_id": "rubric:case_check",
                "objective_ids": ["obj:apply_check"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": [source["id"]],
            }
        ],
        "rubrics": [
            rubric("rubric:practice_check", "criterion:practice_correct"),
            rubric("rubric:case_check", "criterion:case_correct"),
        ],
    }
    qa = {
        "artifact_version": "1.0",
        "source_refs": [source],
        "checked_artifact_ids": ["plan:safety", "lesson:safety_check"],
        "issues": [],
        "verdict": "pass",
        "coverage_score": 1,
        "grounding_score": 1,
        "difficulty_score": 1,
        "assessment_quality_score": 1,
        "revision_required_for": [],
        "summary": "All checks passed",
    }
    scripts = {
        "ingestion_agent_prompt.j2": ingestion,
        "competency_mapper_prompt.j2": competency_map,
        "course_architect_prompt.j2": plan,
        "lesson_writer_prompt.j2": writer,
        "assessment_agent_prompt.j2": assessment,
        "critic_qa_prompt.j2": qa,
    }
    calls = []

    def generate(template_name, **kwargs):
        calls.append(template_name)
        return scripts[template_name]

    runtime = AgentRuntime(
        db=db_session,
        run_id=run.id,
        course_id=course.id,
        generate=generate,
    )
    checkpoints = []
    result = AgenticCoursePipeline(
        runtime=runtime,
        checkpoint=lambda stage, progress: checkpoints.append((stage, progress)),
    ).run(
        course_title="Safety",
        settings_snapshot={
            "goal": "Apply the safety check",
            "target_audience": "Operators",
            "difficulty": "basic",
            "language": "en",
            "lesson_count": 1,
            "module_tests_enabled": True,
            "final_test_enabled": True,
        },
        source_catalog=[source],
    )

    assert calls == list(scripts)
    assert [item[0] for item in checkpoints] == [
        "ingestion",
        "competency_mapping",
        "course_architecture",
        "lesson_writing",
        "assessment_generation",
        "quality_assurance",
        "materialization",
    ]
    assert result.qa_summary["verdict"] == "pass"
    assert sum(item["type"] == "lesson" for item in result.nodes) == 1
    assert sum(item["type"] == "test" for item in result.nodes) == 2
    assert db_session.query(AgentArtifact).count() == 6


def test_agent_artifact_and_update_proposal_endpoints_are_owner_scoped(
    client, db_session, auth_user, auth_headers
):
    owned = make_course(db_session, owner_id=auth_user.id)
    run = GenerationRun(
        owner_id=auth_user.id,
        course_id=owned.id,
        run_type="graph_generation",
        status="running",
        input_docs=[],
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        AgentArtifact(
            run_id=run.id,
            course_id=owned.id,
            agent="ingestion",
            artifact="document_knowledge",
            sequence=0,
            status="completed",
            payload={"ok": True},
        )
    )
    foreign = User(
        email="agent-artifact-foreign@example.com",
        password_hash=hash_password("password123"),
    )
    db_session.add(foreign)
    db_session.flush()
    foreign_course = make_course(db_session, owner_id=foreign.id)
    foreign_run = GenerationRun(
        owner_id=foreign.id,
        course_id=foreign_course.id,
        run_type="graph_generation",
        status="running",
        input_docs=[],
    )
    db_session.add(foreign_run)
    db_session.commit()

    artifacts = client.get(
        f"/api/generation-runs/{run.id}/artifacts", headers=auth_headers
    )
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["payload"] == {"ok": True}
    assert client.get(
        f"/api/generation-runs/{foreign_run.id}/artifacts", headers=auth_headers
    ).status_code == 404

    proposals = client.get(
        f"/api/courses/{owned.id}/update-proposals", headers=auth_headers
    )
    assert proposals.status_code == 200
    assert proposals.json()["items"] == []
    assert client.get(
        f"/api/courses/{foreign_course.id}/update-proposals", headers=auth_headers
    ).status_code == 404


def test_update_agent_proposes_diff_without_mutating_course(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    old = Document(
        storage_key="old.txt",
        owner_id=auth_user.id,
        course_id=course.id,
        document_key="policy",
        version=1,
        is_current=False,
        status="indexed",
        content_hash="old-hash",
        source_type="upload",
        original_filename="policy.txt",
        mime_type="text/plain",
        size_bytes=3,
    )
    db_session.add(old)
    db_session.flush()
    new = Document(
        storage_key="new.txt",
        owner_id=auth_user.id,
        course_id=course.id,
        document_key="policy",
        version=2,
        is_current=True,
        supersedes_document_id=old.id,
        status="indexed",
        content_hash="new-hash",
        source_type="upload",
        original_filename="policy.txt",
        mime_type="text/plain",
        size_bytes=3,
    )
    db_session.add(new)
    db_session.flush()
    old_chunk = DocumentChunk(
        document_id=old.id,
        document_version=1,
        text="Old rule",
        chunk_index=0,
        metadata_json={},
    )
    new_chunk = DocumentChunk(
        document_id=new.id,
        document_version=2,
        text="New rule",
        chunk_index=0,
        metadata_json={},
    )
    db_session.add_all([old_chunk, new_chunk])
    db_session.flush()
    graph_run = GenerationRun(
        owner_id=auth_user.id,
        course_id=course.id,
        run_type="graph_generation",
        status="completed",
        input_docs=[],
    )
    update_run = GenerationRun(
        owner_id=auth_user.id,
        course_id=course.id,
        document_id=new.id,
        run_type="document_index",
        status="succeeded",
        input_docs=[],
    )
    db_session.add_all([graph_run, update_run])
    db_session.flush()
    old_ref_id = f"src:doc:{old.id}:v1:chunk:{old_chunk.id}"
    new_ref_id = f"src:doc:{new.id}:v2:chunk:{new_chunk.id}"
    graph = CourseGraph(
        course_id=course.id,
        version=1,
        nodes=[
            {
                "id": "lesson:one",
                "type": "lesson",
                "label": "Rule",
                "content": "Old rule",
                "source_refs": [old_ref_id],
            }
        ],
        edges=[],
        created_by=auth_user.id,
        status="draft",
    )
    db_session.add(graph)
    db_session.flush()
    course.current_graph = graph
    db_session.add(
        CourseSourceLink(
            course_id=course.id,
            graph_id=graph.id,
            run_id=graph_run.id,
            node_id="lesson:one",
            target_type="lesson",
            document_id=old.id,
            document_version=1,
            chunk_id=old_chunk.id,
            chunk_index=0,
            excerpt="Old rule",
            excerpt_hash="old-excerpt-hash",
            ref_id=old_ref_id,
            relation="supports",
        )
    )
    db_session.commit()
    original_nodes = list(graph.nodes)

    def generate(*args, **kwargs):
        return {
            "artifact_version": "1.0",
            "previous_source_refs": [
                {
                    "id": old_ref_id,
                    "document_id": old.id,
                    "document_version": 1,
                    "document_content_hash": "old-hash",
                    "chunk_id": old_chunk.id,
                    "chunk_index": 0,
                    "page": None,
                    "section": None,
                    "quote": "Old rule",
                }
            ],
            "current_source_refs": [
                {
                    "id": new_ref_id,
                    "document_id": new.id,
                    "document_version": 2,
                    "document_content_hash": "new-hash",
                    "chunk_id": new_chunk.id,
                    "chunk_index": 0,
                    "page": None,
                    "section": None,
                    "quote": "New rule",
                }
            ],
            "document_changes": [
                {
                    "document_id": new.id,
                    "change_type": "modified",
                    "before_version": 1,
                    "before_content_hash": "old-hash",
                    "after_version": 2,
                    "after_content_hash": "new-hash",
                }
            ],
            "impacts": [
                {
                    "id": "impact:rule_change",
                    "affected_artifact_type": "lesson",
                    "affected_artifact_id": "lesson:one",
                    "impact": "high",
                    "proposed_action": "regenerate",
                    "reason": "The cited rule changed.",
                    "confidence": 1,
                    "before_source_ref_ids": [old_ref_id],
                    "after_source_ref_ids": [new_ref_id],
                }
            ],
            "proposed_diff": [
                {
                    "id": "diff:rule_change",
                    "operation": "replace",
                    "target_artifact_type": "lesson",
                    "target_artifact_id": "lesson:one",
                    "json_pointer": "/content",
                    "before": "Old rule",
                    "after": "New rule",
                    "impact_ids": ["impact:rule_change"],
                    "rationale": "Align the lesson with the new rule.",
                }
            ],
            "requires_human_review": True,
            "summary": "One lesson needs review.",
        }

    proposal = CourseUpdateService.analyze_replacement(
        db_session,
        document=new,
        run=update_run,
        generate=generate,
    )

    assert proposal is not None
    assert proposal.status == "proposed"
    assert proposal.affected_node_ids == ["lesson:one"]
    assert db_session.query(CourseUpdateProposal).count() == 1
    db_session.refresh(graph)
    assert graph.nodes == original_nodes
