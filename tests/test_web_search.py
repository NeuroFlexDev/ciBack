import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.ai import web_search
from app.core.config import settings
from app.models.agent_artifact import AgentArtifact
from app.models.ai_resources import AIResourceUsage
from app.models.ai_state import AICall
from app.models.document import Document
from app.models.generation_run import GenerationRun
from app.schemas.agentic_pipeline import SourceRef
from app.services import web_search_service as service
from app.services.file_storage import get_file_storage
from app.services.generation_control import GenerationCancelled
from tests.factories import make_course


def response():
    text = "Evaporation turns liquid water into water vapour. [NASA]"
    return {"status": "completed", "usage": {"input_tokens": 9553, "output_tokens": 222},
        "output": [{"type": "web_search_call", "status": "completed", "action": {"type": "search"}},
            {"type": "message", "content": [{"type": "output_text", "text": text,
                "annotations": [{"type": "url_citation", "url": "https://science.nasa.gov/water-cycle/",
                    "title": "Water cycle", "start_index": text.index("[NASA]"), "end_index": len(text)}]}]}]}


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", False)
    monkeypatch.setattr(settings, "VSELLM_API_KEY", SecretStr("test-key"))
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)


@pytest.fixture
def runtime(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    run = GenerationRun(owner_id=auth_user.id, course_id=course.id, run_type="graph_generation", status="running")
    db_session.add(run); db_session.commit()
    return SimpleNamespace(db=db_session, run_id=run.id, course_id=course.id, owner_id=auth_user.id)


def test_live_compatible_transport_bounds_and_provider_citations(enabled):
    captured = []
    def transport(request):
        captured.append(json.loads(request.content))
        assert request.url.path.endswith("/responses")
        return httpx.Response(200, json=response())
    result = web_search.search("Water cycle", transport=httpx.MockTransport(transport))
    assert result["sources"][0]["url"] == "https://science.nasa.gov/water-cycle/"
    assert result["usage"]["input_tokens"] == 9553
    assert not result["provider_cost_reported"]
    assert len(captured) == 1
    assert captured[0]["max_tool_calls"] == 1 and captured[0]["store"] is False
    assert captured[0]["max_output_tokens"] <= 2000


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/key", "https://user:secret@example.com", "https://metadata.internal/x", "javascript:alert(1)", "https://example.com:9000/x"])
def test_private_or_executable_citation_urls_rejected(url):
    assert web_search.public_url(url) is None


def test_model_markdown_link_is_not_evidence():
    data = response()
    data["output"][1]["content"][0]["annotations"] = []
    with pytest.raises(web_search.SearchUnavailable, match="no_citations") as error:
        web_search.parse_response(data)
    assert error.value.request_sent
    assert error.value.ai_usage["input_tokens"] == 9553


def test_separate_claims_in_one_paragraph_keep_their_own_sources():
    text = "Only A supports first claim. [A] Only B supports second claim. [B] [C]"
    data = response()
    part = data["output"][1]["content"][0]
    part["text"] = text
    part["annotations"] = [{"type": "url_citation", "url": f"https://example.com/{letter}",
        "start_index": text.index(f"[{letter}]"), "end_index": text.index(f"[{letter}]") + 3}
        for letter in "ABC"]
    sources = web_search.parse_response(data)["sources"]
    assert sources[0]["summary"] == "Only A supports first claim."
    assert sources[1]["summary"] == sources[2]["summary"] == "Only B supports second claim."


def test_incomplete_or_multiple_search_calls_not_accepted():
    data = response()
    data["output"].append({"type": "web_search_call", "status": "completed"})
    with pytest.raises(web_search.SearchUnavailable, match="incomplete"):
        web_search.parse_response(data)


def test_timeout_is_not_retried_and_provider_error_is_sanitized(enabled):
    calls = []
    def transport(request):
        calls.append(1)
        raise httpx.ReadTimeout("secret provider body", request=request)
    with pytest.raises(web_search.SearchUnavailable, match="timeout") as error:
        web_search.search("Water cycle", transport=httpx.MockTransport(transport))
    assert calls == [1] and error.value.request_sent
    assert "secret" not in str(error.value)


def test_local_only_never_sends(enabled, monkeypatch):
    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", True)
    with pytest.raises(web_search.SearchUnavailable, match="unconfigured"):
        web_search.search("Water cycle", transport=httpx.MockTransport(lambda _: pytest.fail("External request")))


def test_short_topics_never_include_documents_or_goal(enabled):
    common = {"course_title": "Water cycle", "goal": "PRIVATE_INTERNAL_DOCUMENT", "lesson_count": 3}
    plan = service.search_plan(common, [{"quote": "PRIVATE_INTERNAL_DOCUMENT"}])
    assert plan["queries"] == ["Water cycle"]
    assert service.search_plan({**common, "web_search_enabled": False}, [])["status"] == "disabled"
    assert service.search_plan(common, [{"quote": "Water cycle " * 200}])["status"] == "not_needed"
    assert service.search_plan({**common, "approved_plan": ["Unrelated planets"]}, [{"quote": "Water cycle " * 200}])["reason"] == "uncovered_plan_topics"
    assert "secret@example.com" not in web_search.short_topic("Water secret@example.com https://internal/key")


def test_snapshot_provenance_receipt_resume_and_pdf_markdown(enabled, runtime):
    calls = []
    def invoke(query):
        calls.append(query)
        return web_search.parse_response(response())
    common = {"course_title": "Water cycle", "lesson_count": 2}
    catalog = []
    service.supplement(runtime, common, catalog, invoke=invoke)
    assert calls == ["Water cycle"]
    assert len(catalog) == 1
    ref = SourceRef.model_validate(catalog[0])
    assert ref.source_type == "web_search" and ref.evidence_kind == "provider_summary"
    doc = runtime.db.get(Document, ref.document_id)
    assert doc.source_type == "web_search" and not doc.learner_visible
    persisted = json.loads(get_file_storage().read_bytes(doc.storage_key))
    assert persisted["source_url"] == ref.source_url
    ledger = runtime.db.query(AIResourceUsage).filter_by(operation="web_search").one()
    assert ledger.status == "completed" and ledger.tokens == 9775 and ledger.cost_rub == Decimal("20")
    assert runtime.db.query(AICall).filter_by(agent="web_search").one().input_tokens == 9553
    again = []
    service.supplement(runtime, common, again, invoke=invoke)
    assert again == catalog and calls == ["Water cycle"]
    markdown = service.citation_markdown([ref])
    assert "https://science.nasa.gov/water-cycle/" in markdown and "Интернет-источники" in markdown
    assert service.status(runtime.db, runtime.run_id)["sources"][0]["url"] == ref.source_url


def test_timeout_receipt_retained_and_resume_never_resends(enabled, runtime):
    calls = []
    def invoke(_):
        calls.append(1)
        raise web_search.SearchUnavailable("web_search_timeout", request_sent=True)
    common = {"course_title": "Water cycle"}
    service.supplement(runtime, common, [], invoke=invoke)
    service.supplement(runtime, common, [], invoke=invoke)
    assert calls == [1]
    record = runtime.db.query(AIResourceUsage).filter_by(operation="web_search").one()
    assert record.status == "unknown" and record.tokens >= 32000 and record.cost_rub == Decimal("20")
    assert service.status(runtime.db, runtime.run_id)["status"] == "unavailable"


def test_budget_exhaustion_does_not_send(enabled, runtime, monkeypatch):
    monkeypatch.setattr(settings, "AI_MAX_RUB_PER_RUN", Decimal("1"))
    service.supplement(runtime, {"course_title": "Water cycle"}, [], invoke=lambda _: pytest.fail("Paid call"))
    assert service.status(runtime.db, runtime.run_id)["status"] == "unavailable"
    assert runtime.db.query(AICall).count() == 0


def test_worker_crash_intent_never_resends(enabled, runtime):
    runtime.db.add(AgentArtifact(run_id=runtime.run_id, course_id=runtime.course_id, agent="web_search", artifact="web_search_evidence", sequence=0,
        status="failed", payload={"status": "searching", "queries": ["Water cycle"], "catalog": []}))
    runtime.db.commit()
    service.supplement(runtime, {"course_title": "Water cycle"}, [], invoke=lambda _: pytest.fail("Paid duplicate"))
    assert service.status(runtime.db, runtime.run_id)["status"] == "unknown"


def test_revocation_during_search_preserves_receipt_but_no_materials(enabled, runtime, auth_user):
    def invoke(_):
        auth_user.is_active = False
        runtime.db.commit()
        return web_search.parse_response(response())
    with pytest.raises(GenerationCancelled, match="отозван"):
        service.supplement(runtime, {"course_title": "Water cycle"}, [], invoke=invoke)
    assert runtime.db.query(Document).count() == 0
    assert runtime.db.query(AIResourceUsage).filter_by(operation="web_search").one().tokens == 9775


def test_uploaded_source_cannot_impersonate_web_evidence():
    source = {"id": "src:doc:1:v1:chunk:1", "document_id": 1, "document_version": 1,
        "document_content_hash": "hash", "chunk_id": 1, "chunk_index": 0, "quote": "Text", "source_url": "https://example.com"}
    with pytest.raises(ValueError, match="impersonate"):
        SourceRef.model_validate(source)


def test_search_choice_is_frozen_and_idempotent(client, db_session, auth_user, auth_headers, enabled, monkeypatch):
    from tests.test_async_generation_launch import _settings, _document
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    queued = []
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", queued.append)
    payload = {"settings": _settings(), "document_ids": [document.id], "request_id": "web-optout-123", "web_search_enabled": False}
    preflight = client.post(f"/api/courses/{course.id}/generation-preflight", headers=auth_headers, json=payload)
    assert preflight.status_code == 200 and preflight.json()["web_search"]["status"] == "disabled"
    first = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload)
    assert first.status_code == 202
    run = db_session.get(GenerationRun, first.json()["run_id"])
    assert run.settings_snapshot["web_search_enabled"] is False
    second = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload)
    assert second.status_code == 202 and second.json()["run_id"] == run.id
    changed = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json={**payload, "web_search_enabled": True})
    assert changed.status_code == 409 and len(queued) == 1
    # Existing clients can recover a legacy receipt, but a newly explicit true
    # toggle must not silently attach search to that frozen old request.
    run.settings_snapshot = {key: value for key, value in run.settings_snapshot.items() if key != "web_search_enabled"}
    db_session.commit()
    legacy = {key: value for key, value in payload.items() if key != "web_search_enabled"}
    assert client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=legacy).status_code == 202
    assert client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json={**legacy, "web_search_enabled": True}).status_code == 409


def test_source_references_and_hash_stay_compatible():
    source = {"id": "src:doc:1:v1:chunk:1", "document_id": 1, "document_version": 1,
        "document_content_hash": "hash", "chunk_id": 1, "chunk_index": 0, "page": None, "section": None, "quote": "Text"}
    assert SourceRef.model_validate(source).model_dump(mode="json") == source


def test_legacy_pipeline_snapshot_never_admits_new_search(enabled, runtime, monkeypatch):
    from app.services.agent_runtime import AgentRuntime
    from app.services.agentic_course_pipeline import AgenticCoursePipeline
    actual = AgentRuntime(db=runtime.db, run_id=runtime.run_id, course_id=runtime.course_id, generate=lambda **_: pytest.fail("AI call"))
    monkeypatch.setattr(web_search, "search", lambda _: pytest.fail("Legacy job unexpectedly searched"))
    def reached_ingestion(*_):
        raise RuntimeError("reached original ingestion")
    monkeypatch.setattr(AgenticCoursePipeline, "_ingest", reached_ingestion)
    with pytest.raises(RuntimeError, match="reached original ingestion"):
        AgenticCoursePipeline(runtime=actual, checkpoint=lambda *_: None).run(course_title="Water cycle",
            settings_snapshot={"goal": "Learn", "difficulty": "basic", "language": "en", "lesson_count": 1},
            source_catalog=[])
    assert service.status(runtime.db, runtime.run_id)["status"] == "disabled"
    assert runtime.db.query(AICall).count() == 0
