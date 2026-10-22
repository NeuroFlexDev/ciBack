"""Independent regressions using real templates and the bundled offline tokenizer."""
import copy
import json
from types import SimpleNamespace

import pytest
import requests
import tiktoken
import tiktoken.load
from tiktoken.registry import ENCODINGS

from app.ai import context_budget as budget
from app.ai.knowledge_batches import ingestion_excerpt, merge_competencies, partition_records
from app.core.config import settings
from app.schemas.agentic_pipeline import CompetencyMapArtifact, IngestionArtifact
from app.services.generation_service import agent_messages, render_prompt


MODEL = "openai/gpt-5.4-mini"
TEMPLATE = "competency_mapper_prompt.j2"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Token accounting must never download data at runtime")
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(tiktoken.load, "read_file", forbidden)
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [])
    monkeypatch.setattr(settings, "AI_MODEL_REASONING", MODEL)
    budget._openai_encoding.cache_clear()
    yield
    budget._openai_encoding.cache_clear()


def inventory(count):
    refs = [{"id": f"src:doc:1:v1:chunk:{i + 1}", "document_id": 1,
             "document_version": 1, "document_content_hash": "immutable-russian-source",
             "chunk_id": i + 1, "chunk_index": i,
             "quote": f"Условие {i}: " + "Давление не выше восьми бар, кроме проверки на стенде. " * 5}
            for i in range(count)]
    records = [{"id": f"kn:rule-{i:03d}", "kind": "requirement", "title": f"Правило {i}",
                "statement": f"Запись {i}. " + "Сохраняйте исключения, единицы измерения и последовательность действий. " * 8,
                "source_ref_ids": [ref["id"]], "related_item_ids": [],
                "attributes": {"threshold": i, "exception": "Только на испытательном стенде"}}
               for i, ref in enumerate(refs)]
    return IngestionArtifact.model_validate({"source_refs": refs, "knowledge_items": records,
        "documents": [{"document_id": 1, "document_version": 1, "title": "Русский регламент",
                       "summary": "Все условия и исключения сохраняются.",
                       "source_ref_ids": [ref["id"] for ref in refs],
                       "knowledge_item_ids": [record["id"] for record in records]}]}).model_dump(mode="json")


def context_for(ingestion, records):
    return {"lesson_count": 6, "language": "ru", "external_context": "",
            "ingestion_artifact_json": json.dumps(ingestion_excerpt(ingestion, records), ensure_ascii=False),
            "qa_feedback_json": "null"}


@pytest.mark.parametrize("count", [125, 134])
def test_real_russian_inventory_partitions_without_losing_facts_or_citations(monkeypatch, count):
    assert budget._openai_encoding() is not None, "Prepare o200k cache at build/test setup, never in this test"
    monkeypatch.setattr(settings, "AI_MAX_INPUT_TOKENS", 12000)
    ingestion = inventory(count)
    before = copy.deepcopy(ingestion)
    batches = partition_records(ingestion["knowledge_items"], lambda records: context_for(ingestion, records),
                                role="competency_mapper", template=TEMPLATE)
    assert len(batches) > 1
    assert [record for batch in batches for record in batch] == before["knowledge_items"]
    seen_sources = []
    for batch in batches:
        excerpt = ingestion_excerpt(ingestion, batch)
        IngestionArtifact.model_validate(excerpt)
        seen_sources.extend(excerpt["source_refs"])
        messages = agent_messages(render_prompt(TEMPLATE, **context_for(ingestion, batch)), expect_json=True)
        estimate = budget.require_fits(messages, MODEL, 10000)
        assert estimate <= 12000
        assert budget.prompt_fits("competency_mapper", TEMPLATE, context_for(ingestion, batch))
    assert seen_sources == before["source_refs"]
    assert ingestion == before


def test_real_tokenizer_counts_russian_text_without_network_or_character_heuristic():
    # Drop the process registry entry to exercise the on-disk cache path too.
    ENCODINGS.pop("o200k_base", None)
    encoding = budget._openai_encoding()
    assert encoding is not None, "CI and Docker must prewarm the tokenizer cache"
    messages = [{"role": "user", "content": "Проверка исключений и ограничений давления. " * 200}]
    actual = budget.count_messages(messages, MODEL)
    raw_tokens = len(encoding.encode(json.dumps(messages, ensure_ascii=False, separators=(",", ":"))))
    assert actual >= raw_tokens
    assert actual < len(messages[0]["content"].encode("utf-8")) // 2


@pytest.mark.parametrize("cache_state", ["missing", "corrupt"])
def test_missing_or_corrupt_tokenizer_cache_uses_conservative_offline_bound(monkeypatch, tmp_path, cache_state):
    import hashlib
    ENCODINGS.pop("o200k_base", None)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    if cache_state == "corrupt":
        (tmp_path / hashlib.sha1(budget.TOKENIZER_URL.encode()).hexdigest()).write_bytes(b"broken-cache")
    monkeypatch.setattr(tiktoken, "get_encoding", lambda *a, **k: pytest.fail("invalid cache must not reach downloader"))
    messages = [{"role": "user", "content": "Русский текст с важным условием"}]
    raw = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert budget.count_messages(messages, MODEL) >= len(raw)


def test_output_reservation_and_fallback_model_caps_are_checked(monkeypatch):
    messages = [{"role": "user", "content": "Не отбрасывать исключение"}]
    fallback = "fallback/offline-test"
    estimate = budget.count_messages(messages, fallback)
    monkeypatch.setattr(settings, "AI_MAX_INPUT_TOKENS", 64000)
    monkeypatch.setattr(settings, "AI_ROUTING_CANDIDATES", [SimpleNamespace(
        model=fallback, provider="vsellm", max_input_tokens=estimate,
        max_output_tokens=50, context_window=estimate + 40)])
    assert budget.limits(fallback) == (estimate, 50, estimate + 40)
    assert budget.require_fits(messages, fallback, 40) == estimate
    with pytest.raises(budget.ContextBudgetExceeded) as error:
        budget.require_fits(messages, fallback, 41)
    assert error.value.request_sent is False
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.require_fits(messages, fallback, 51)
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.require_fits(messages + [{"role": "user", "content": "Ещё условие"}], fallback, 1)
    assert budget.require_fits(messages, MODEL, 10000) > 0
    monkeypatch.setattr(settings, "AI_MAX_INPUT_TOKENS", 1000)
    assert budget.limits("unregistered-model") == (1000, 1000, 1000)
    with pytest.raises(budget.ContextBudgetExceeded):
        budget.require_fits(messages, "unregistered-model", 1000)


def test_indivisible_record_fails_explicitly_without_mutation(monkeypatch):
    monkeypatch.setattr(settings, "AI_MAX_INPUT_TOKENS", 1000)
    ingestion = inventory(1)
    before = copy.deepcopy(ingestion)
    with pytest.raises(budget.ContextBudgetExceeded) as error:
        partition_records(ingestion["knowledge_items"], lambda batch: context_for(ingestion, batch),
                          role="competency_mapper", template=TEMPLATE)
    assert error.value.status_code == 413 and error.value.request_sent is False
    assert ingestion == before


def test_colliding_competency_ids_get_independent_connected_namespaces():
    source = inventory(1)["source_refs"][0]
    def part(index):
        base = {"title": "Одинаковое название", "description": "Разные факты сохраняются", "source_ref_ids": [source["id"]]}
        leaf = {**base, "source_knowledge_item_ids": [f"kn:fact-{index}"]}
        return CompetencyMapArtifact.model_validate({"source_refs": [source], "source_knowledge_item_ids": [f"kn:fact-{index}"],
            "roles": [{**base, "id": "role:same", "competency_ids": ["cmp:same"]}],
            "competencies": [{**base, "id": "cmp:same", "level": "basic", "skill_ids": ["skill:same"]}],
            "skills": [{**base, "id": "skill:same", "knowledge_ids": ["know:same"], "procedure_ids": ["proc:same"]}],
            "knowledge": [{**leaf, "id": "know:same"}],
            "procedures": [{**leaf, "id": "proc:same", "steps": [f"Неповторимое действие {index}"]}]})
    parts = [part(0), part(1)]
    before = [p.model_dump() for p in parts]
    merged = merge_competencies(parts)
    for index in range(2):
        assert merged.roles[index].competency_ids == [merged.competencies[index].id]
        assert merged.competencies[index].skill_ids == [merged.skills[index].id]
        assert merged.skills[index].knowledge_ids == [merged.knowledge[index].id]
        assert merged.skills[index].procedure_ids == [merged.procedures[index].id]
        assert merged.knowledge[index].source_knowledge_item_ids == [f"kn:fact-{index}"]
        assert merged.procedures[index].steps == [f"Неповторимое действие {index}"]
    for field in ("roles", "competencies", "skills", "knowledge", "procedures"):
        assert len({row.id for row in getattr(merged, field)}) == 2
    assert [ref.model_dump(mode="json") for ref in merged.source_refs] == [source]
    assert merged.source_knowledge_item_ids == ["kn:fact-0", "kn:fact-1"]
    assert [p.model_dump() for p in parts] == before
