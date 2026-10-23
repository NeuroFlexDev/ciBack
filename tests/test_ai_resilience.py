from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.ai import gateway, providers
from app.core.config import AIProvider, AIRoutingCandidate, settings
from app.models.ai_state import AICall
from app.models.generation_run import GenerationRun
from app.models.chat import ChatMessage
from app.repositories.chat import ChatRepository
from app.services.chat_service import chat_generate
from app.services.pipeline_service import PipelineService
from tests.factories import make_course
from tests.test_generation_status import _run
from tests.test_ai_runtime_v2 import execute, make_runtime


@pytest.fixture
def two_providers(monkeypatch):
    providers._circuits.clear()
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "adaptive")
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr(""))
    monkeypatch.setenv("ALPHA_TEST_KEY", "alpha-test-only")
    monkeypatch.setenv("BETA_TEST_KEY", "beta-test-only")
    monkeypatch.setattr(settings, "AI_PROVIDERS", [
        AIProvider(id=name, base_url=f"https://{name}.example/v1", api_key_env=f"{name.upper()}_TEST_KEY")
        for name in ("alpha", "beta")])
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [AIRoutingCandidate(
        provider=name, model="test/shared", roles=["chat", "ingestion"], approved=True,
        input_rub_per_million=rate, output_rub_per_million=rate,
        max_input_tokens=64000, max_output_tokens=16000, context_window=100000)
        for name, rate in [("alpha", 1), ("beta", 2)]])
    yield
    providers._circuits.clear()


def response():
    return SimpleNamespace(content="ok", response_metadata={}, additional_kwargs={},
                           usage_metadata={"input_tokens": 10, "output_tokens": 2})


def test_cheapest_provider_is_used_on_the_first_normal_call(two_providers, monkeypatch):
    seen = []
    class Model:
        def __init__(self, **kwargs):
            seen.append((kwargs["base_url"], kwargs["api_key"]))
        def invoke(self, messages): return response()
    monkeypatch.setattr(gateway, "ChatOpenAI", Model)
    answer = gateway.invoke_messages("chat", [{"role": "user", "content": "hello"}])
    assert answer["provider"] == "alpha"
    assert seen == [("https://alpha.example/v1", "alpha-test-only")]


def test_failover_uses_another_provider_and_circuit_applies_to_next_request(two_providers, monkeypatch):
    seen = []
    class Model:
        def __init__(self, **kwargs): self.url = kwargs["base_url"]
        def invoke(self, messages):
            seen.append(self.url)
            if "alpha" in self.url: raise TimeoutError("do not expose transport detail")
            return response()
    monkeypatch.setattr(gateway, "ChatOpenAI", Model)
    first = gateway.chat_completion("chat", [{"role": "user", "content": "hello"}])
    second = gateway.chat_completion("chat", [{"role": "user", "content": "hello again"}])
    assert first["provider"] == second["provider"] == "beta"
    assert seen == ["https://alpha.example/v1", "https://beta.example/v1", "https://beta.example/v1"]


def test_local_provider_can_answer_without_external_credentials(monkeypatch):
    monkeypatch.setattr(settings, "AI_ROUTING_MODE", "adaptive")
    monkeypatch.setattr(settings, "AI_PROVIDERS", [AIProvider(id="local", local=True, base_url="http://127.0.0.1:11434/v1")])
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [AIRoutingCandidate(provider="local", model="local-test",
        roles=["chat"], approved=True, input_rub_per_million=0, output_rub_per_million=0,
        max_input_tokens=16000, max_output_tokens=4000, context_window=20000)])
    class Model:
        def __init__(self, **kwargs): assert kwargs["base_url"] == "http://127.0.0.1:11434/v1"
        def invoke(self, messages): return response()
    monkeypatch.setattr(gateway, "ChatOpenAI", Model)
    assert gateway.chat_completion("chat", [{"role": "user", "content": "hello"}])["provider"] == "local"


def test_route_approval_does_not_transfer_to_another_provider(two_providers, monkeypatch):
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr("vsellm-test-only"))
    for name in ("alpha", "beta"):
        providers.mark_unavailable(providers.registry()[name])
    monkeypatch.setattr(gateway, "ChatOpenAI", lambda **kw: pytest.fail("unapproved route must not send"))
    with pytest.raises(providers.AIUnavailable):
        gateway.invoke_messages("chat", [{"role": "user", "content": "hello"}], model="test/shared")


def test_chat_has_persisted_local_answer_with_no_inference_service(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(gateway, "ChatOpenAI", lambda **kw: pytest.fail("must not call network"))
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Offline")
    result = chat_generate(chat_id=chat.id, user_id=auth_user.id, text="Объясни определение", db=db_session)
    assert result["raw"]["degraded"]
    assert "локальный режим" in result["answer"]
    assert db_session.query(ChatMessage).filter_by(chat_id=chat.id).count() == 2


def test_local_answer_quotes_only_accessible_sources(db_session, auth_user, monkeypatch):
    from tests.test_acl_retrieval import _document
    course = make_course(db_session, owner_id=auth_user.id)
    _, chunk = _document(db_session, course, auth_user.id, "rule.txt")
    chunk.text = "При утечке оператор нажимает аварийную кнопку."
    db_session.commit()
    chat = ChatRepository.create_chat(db_session, auth_user.id, "Sources", course.id)
    result = chat_generate(chat_id=chat.id, user_id=auth_user.id, text="Что делать при утечке?", db=db_session)
    assert result["raw"]["degraded"]
    assert chunk.text in result["answer"]
    assert f"[rule.txt](/courses/{course.id}/edit)" in result["answer"]


def test_unsent_unavailable_call_does_not_consume_token_reservation(db_session, auth_user, monkeypatch):
    from app.services import generation_service
    monkeypatch.setattr(gateway, "configured", lambda: True)
    monkeypatch.setattr(generation_service, "render_prompt", lambda *a, **kw: "prompt")
    runtime = make_runtime(db_session, auth_user, lambda *a, **kw: (_ for _ in ()).throw(providers.AIUnavailable()))
    runtime.max_attempts = 1
    with pytest.raises(providers.AIUnavailable): execute(runtime)
    call = db_session.query(AICall).one()
    assert (call.status, call.input_tokens, call.output_tokens) == ("unavailable", 0, 0)


def test_paused_run_is_not_an_error_and_resumes_same_checkpoint_id(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    run = _run(db_session, auth_user, course, status="paused", current_stage="lesson_writing",
               progress_percent=60, retryable=True, error="private diagnostic", error_code="ai_unavailable")
    body = client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).json()
    assert body["status"] == "paused" and body["status_error"] is None and body["error"] is None
    assert "сохранены" in body["status_notice"]
    resumed = PipelineService.retry_graph_generation(db_session, run.id, auth_user.id)
    assert resumed.id == run.id and resumed.status == "queued" and resumed.progress_percent == 60
    assert db_session.query(GenerationRun).count() == 1


def test_canvas_outage_returns_local_notice_without_fake_nodes(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    monkeypatch.setattr("app.routes.ai_workspace.chat_completion",
                        lambda *a, **kw: (_ for _ in ()).throw(providers.AIUnavailable()))
    result = client.post(f"/api/courses/{course.id}/ai/actions", headers=auth_headers,
                         json={"action": "find_gaps", "selected_nodes": [{"title": "Тема"}]})
    assert result.status_code == 200
    assert result.json()["degraded"] and result.json()["nodes"] == []


def test_embedding_outage_keeps_chunks_searchable(db_session, auth_user, monkeypatch):
    from app.ai import retrieval
    from app.services.retrieval_service import RetrievalService
    from tests.test_acl_retrieval import _document
    course = make_course(db_session, owner_id=auth_user.id)
    _, chunk = _document(db_session, course, auth_user.id, "rule.txt")
    chunk.text = "При утечке нажать аварийную кнопку."
    monkeypatch.setattr(gateway, "available_provider_ids", lambda: {"vsellm"})
    class BrokenEmbeddings:
        def embed_documents(self, texts): raise TimeoutError("upstream")
    monkeypatch.setattr(retrieval, "embedding_client", lambda: BrokenEmbeddings())
    assert retrieval.persist_embeddings(db_session, [chunk]) == [f"chunk:{chunk.id}"]
    db_session.commit()
    found = RetrievalService.search_course(db_session, course_id=course.id, owner_id=auth_user.id,
        query="Что делать при утечке?", limit=3, vector_store=retrieval.PersistentVectorStore(db_session))
    assert [item.chunk_id for item in found.citations] == [chunk.id]


def test_forecast_requires_owned_course_and_documents(client, db_session, auth_user, auth_headers, monkeypatch):
    from tests.test_acl_retrieval import _document
    monkeypatch.setattr(gateway, "ChatOpenAI", lambda **kw: pytest.fail("forecast must not call AI"))
    course = make_course(db_session, owner_id=auth_user.id)
    document, _ = _document(db_session, course, auth_user.id, "owned.txt")
    payload = {"lesson_count": 3, "document_ids": [document.id]}
    result = client.post(f"/api/courses/{course.id}/ai/forecast", headers=auth_headers, json=payload)
    assert result.status_code == 200
    assert result.json()["lesson_count"] == 3 and result.json()["includes_retries"] is False
    foreign = make_course(db_session)
    assert client.post(f"/api/courses/{foreign.id}/ai/forecast", headers=auth_headers, json=payload).status_code == 404


def test_critic_never_falls_back_to_the_writer_model():
    from app.ai.models import model_policy
    assert model_policy("critic_qa").fallback != settings.AI_MODEL_WRITER


def test_real_worker_without_providers_preserves_a_resumable_run(db_session, auth_user, monkeypatch, tmp_path):
    from app.models.course_generation_settings import CourseGenerationSettings
    from app.models.document import DocumentChunk
    from tests.test_async_generation_launch import _document
    from app.workers.generation import execute_generation_run
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    document.chunks[0].text = "Перед работой проверьте состояние оборудования."
    db_session.add(CourseGenerationSettings(course_id=course.id, goal="Safe operation", difficulty="basic",
        language="ru", lesson_count=1, module_tests_enabled=False, final_test_enabled=False,
        created_by=auth_user.id, updated_by=auth_user.id))
    course.status = "configured"
    db_session.commit()
    run = PipelineService.prepare_graph_generation(db_session, course_id=course.id,
        owner_id=auth_user.id, document_ids=[document.id])
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "AI_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(settings, "AI_CHECKPOINT_SQLITE_PATH", tmp_path / "offline.sqlite")
    # Exercise provider unavailability with an available admission queue. This
    # test deliberately runs production paths without a real Redis service.
    monkeypatch.setattr("app.services.ai_resources.background_admission_paused", lambda: False)
    monkeypatch.setattr("app.workers.generation.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(gateway, "ChatOpenAI", lambda **kw: pytest.fail("must not call network"))
    execute_generation_run(run.id)
    db_session.refresh(run)
    assert (run.status, run.error_code, run.retryable) == ("paused", "ai_unavailable", True)
    assert db_session.query(AICall).filter(AICall.status != "unavailable").count() == 0
    assert PipelineService.retry_graph_generation(db_session, run.id, auth_user.id).id == run.id
