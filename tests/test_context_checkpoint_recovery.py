"""Context-planner upgrades must reuse immutable evidence, not rebill extraction."""
import copy

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.ai import gateway
from app.ai.context_budget import ContextBudgetExceeded
from app.ai.evidence import merge_ingestion
from app.core.config import settings
from app.models.agent_artifact import AgentArtifact
from app.models.ai_state import AICall
from app.schemas.agentic_pipeline import IngestionArtifact
from app.services.agentic_course_pipeline import AgenticCoursePipeline
from app.services.pipeline_service import PipelineService
from tests.test_ai_runtime_v2 import make_runtime, execute, SOURCE


def _payload(source, index):
    return {"source_refs": [source], "documents": [{"document_id": 1,
        "document_version": 1, "title": "Правила", "summary": "Извлечённые факты",
        "source_ref_ids": [source["id"]], "knowledge_item_ids": [f"kn:rule{index}"]}],
        "knowledge_items": [{"id": f"kn:rule{index}", "kind": "requirement",
            "title": f"Правило {index}", "statement": f"Сохранить условие и исключение {index}.",
            "source_ref_ids": [source["id"]], "related_item_ids": [], "attributes": {"limit": index}}],
        "warnings": []}


def test_eight_legacy_ingestion_checkpoints_survive_template_schema_upgrade(db_session, auth_user, monkeypatch):
    runtime = make_runtime(db_session, auth_user, lambda *a, **k: pytest.fail("completed evidence must not be billed again"))
    monkeypatch.setattr(settings, "AI_INGESTION_BATCH_CHARS", 80)
    sources, rows, payloads = [], [], []
    for index in range(8):
        source = {**SOURCE, "id": f"src:doc:1:v1:chunk:{index + 1}", "chunk_id": index + 1,
                  "chunk_index": index, "quote": f"Условие {index}: " + "не удалять исключения. " * 4}
        payload = _payload(source, index)
        sources.append(source)
        payloads.append(IngestionArtifact.model_validate(payload))
        rows.append(AgentArtifact(run_id=runtime.run_id, course_id=runtime.course_id,
            agent="ingestion", artifact="document_knowledge", sequence=index, status="completed",
            payload=payload, input_fingerprint=f"legacy-template-schema-{index}"))
    db_session.add_all(rows)
    db_session.add(AICall(run_id=runtime.run_id, agent="ingestion", model="old-model",
        status="completed", input_tokens=111, output_tokens=222))
    db_session.commit()
    before = [(row.id, row.input_fingerprint, copy.deepcopy(row.payload)) for row in rows]
    runtime.register_sources(sources)
    result = AgenticCoursePipeline(runtime=runtime, checkpoint=lambda *a: None)._ingest(
        {"course_title": "Правила", "difficulty": "basic", "language": "ru"}, sources)
    assert result == merge_ingestion(payloads)
    assert {ref.id for ref in result.source_refs} == {source["id"] for source in sources}
    assert len(result.knowledge_items) == 8
    db_session.expire_all()
    assert [(row.id, row.input_fingerprint, row.payload) for row in rows] == before
    ledger = db_session.query(AICall).all()
    assert len(ledger) == 1 and (ledger[0].input_tokens, ledger[0].output_tokens) == (111, 222)


@pytest.mark.parametrize("change", ["quote", "feedback"])
def test_changed_evidence_or_qa_feedback_cannot_restore_legacy_checkpoint(db_session, auth_user, monkeypatch, change):
    calls = []
    source = copy.deepcopy(SOURCE)
    runtime = make_runtime(db_session, auth_user, lambda *a, **k: calls.append(k) or _payload(source, 0))
    db_session.add(AgentArtifact(run_id=runtime.run_id, course_id=runtime.course_id,
        agent="ingestion", artifact="document_knowledge", sequence=0, status="completed",
        payload=_payload(copy.deepcopy(source), 0), input_fingerprint="legacy"))
    db_session.commit()
    if change == "quote":
        source["quote"] = "Изменённое условие; старый checkpoint больше не совпадает."
    runtime.register_sources([source])
    AgenticCoursePipeline(runtime=runtime, checkpoint=lambda *a: None)._ingest(
        {"course_title": "Правила", "difficulty": "basic", "language": "ru"}, [source],
        {"summary": "Проверить исключения"} if change == "feedback" else None)
    assert len(calls) == 1


def test_context_recovery_is_explicit_and_preserves_run_snapshot_and_ledger(db_session, auth_user):
    from tests.test_generation_status import _run
    from tests.factories import make_course
    course = make_course(db_session, owner_id=auth_user.id)
    snapshot = {"lesson_count": 2, "goal": "Сохранить исходные параметры"}
    run = _run(db_session, auth_user, course, status="failed", retryable=False,
        current_stage="competency_mapping", progress_percent=20,
        error_code="ai_context_exceeded", settings_snapshot=copy.deepcopy(snapshot))
    db_session.add(AICall(run_id=run.id, agent="ingestion", model="old-model",
        status="completed", input_tokens=111, output_tokens=222))
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        PipelineService.retry_graph_generation(db_session, run.id, auth_user.id)
    assert error.value.status_code == 409
    # A scoped operator recovery, after a verified fix, enables one standard
    # user retry. It does not erase the ledger or construct a new run/snapshot.
    run.retryable = True
    db_session.commit()
    resumed = PipelineService.retry_graph_generation(db_session, run.id, auth_user.id)
    assert resumed.id == run.id and resumed.status == "queued" and resumed.attempt == 2
    assert resumed.settings_snapshot == snapshot and resumed.progress_percent == 20
    assert db_session.query(AICall).one().input_tokens == 111
    with pytest.raises(HTTPException):
        PipelineService.retry_graph_generation(db_session, run.id, auth_user.id)


def test_provider_context_error_never_retries_same_prompt_or_exposes_echo(monkeypatch):
    attempts = []
    class ProviderError(Exception):
        status_code = 400
        body = {"error": {"code": "context_length_exceeded", "message": "PRIVATE PROMPT ECHO"}}
    class Rejected:
        def __init__(self, **kw): pass
        def invoke(self, messages):
            attempts.append(messages)
            raise ProviderError()
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr("test-only"))
    monkeypatch.setattr(gateway, "ChatOpenAI", Rejected)
    with pytest.raises(ContextBudgetExceeded) as error:
        gateway.chat_completion("chat", [{"role": "user", "content": "Hello"}])
    assert len(attempts) == 1 and error.value.request_sent
    assert "PRIVATE" not in str(error.value.detail)
    assert not gateway.retryable(error.value)
    assert gateway.safe_error(error.value) == "provider_context_exceeded"


def test_provider_context_error_keeps_unknown_usage_reservation(db_session, auth_user, monkeypatch):
    from app.services import generation_service
    monkeypatch.setattr(gateway, "configured", lambda: True)
    monkeypatch.setattr(generation_service, "render_prompt", lambda *a, **k: "Prompt")
    attempts = []
    def rejected(*a, **k):
        attempts.append(1)
        raise ContextBudgetExceeded(model=settings.AI_MODEL_FAST, input_tokens=100,
            output_tokens=6000, input_limit=64000, context_window=400000, request_sent=True)
    runtime = make_runtime(db_session, auth_user, rejected)
    with pytest.raises(ContextBudgetExceeded): execute(runtime)
    assert len(attempts) == 1
    call = db_session.query(AICall).one()
    assert call.status == "failed" and call.input_tokens > 0 and call.output_tokens == 6000
