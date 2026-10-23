import copy

import pytest
from pydantic import ValidationError

from app.ai.curriculum import complete_writer_inventories
from app.schemas.agentic_pipeline import WriterArtifact
from app.services.agent_runtime import expand_source_refs
from tests.test_ai_runtime_v2 import SOURCE, make_runtime

SECOND = {**SOURCE, 'id': 'src:doc:1:v1:chunk:2', 'chunk_id': 2, 'chunk_index': 1}
CATALOG = [SOURCE, SECOND]


def draft():
    return {'source_refs': [SECOND['id']], 'expected_lesson_ids': ['lesson:one'],
        'objective_ids': ['obj:one'], 'competency_ids': ['cmp:one'], 'lessons': [{
            'id': 'lesson:one', 'title': 'Проверка', 'summary': 'Краткий обзор',
            'objective_ids': ['obj:one'], 'competency_ids': ['cmp:one'],
            'source_ref_ids': [SECOND['id']], 'key_takeaways': ['Давление не выше 8 бар.'],
            'sections': [{'id': 'section:one', 'heading': 'Правило',
                'content_markdown': 'Давление не выше 8 бар.', 'source_ref_ids': [SOURCE['id']]}]}]}


def test_writer_indexes_follow_actual_section_citations_without_changing_claims():
    raw = draft()
    original = copy.deepcopy(raw)
    normalized = complete_writer_inventories(raw, CATALOG)
    result = WriterArtifact.model_validate(expand_source_refs(normalized, {s['id']: s for s in CATALOG}))
    assert result.lessons[0].source_ref_ids == [SOURCE['id']]
    assert [s.id for s in result.source_refs] == [SOURCE['id']]
    assert normalized['lessons'][0]['sections'] == raw['lessons'][0]['sections']
    assert raw == original


@pytest.mark.parametrize('where', ['artifact', 'lesson', 'section', 'forged_quote'])
def test_writer_rejects_unknown_or_altered_evidence_even_in_unused_indexes(where):
    raw = draft()
    if where == 'artifact':
        raw['source_refs'] = ['src:unknown']
    elif where == 'lesson':
        raw['lessons'][0]['source_ref_ids'] = ['src:unknown']
    elif where == 'section':
        raw['lessons'][0]['sections'][0]['source_ref_ids'] = ['src:unknown']
    else:
        raw['source_refs'] = [{**SOURCE, 'quote': 'Altered evidence'}]
    with pytest.raises(ValueError):
        complete_writer_inventories(raw, CATALOG)


@pytest.mark.parametrize('field', ['empty_citations', 'duplicate_objectives', 'duplicate_sections'])
def test_writer_index_recovery_does_not_relax_content_contract(field):
    raw = draft()
    lesson = raw['lessons'][0]
    if field == 'empty_citations':
        lesson['sections'][0]['source_ref_ids'] = []
    elif field == 'duplicate_objectives':
        lesson['objective_ids'] *= 2
    else:
        lesson['sections'] *= 2
    with pytest.raises(ValidationError):
        WriterArtifact.model_validate(expand_source_refs(complete_writer_inventories(raw, CATALOG),
            {s['id']: s for s in CATALOG}))


def test_runtime_accepts_reconciled_writer_in_one_call(db_session, auth_user):
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        return draft()
    runtime = make_runtime(db_session, auth_user, generate)
    runtime.register_sources(CATALOG)
    result = runtime.execute(agent='lesson_writer', artifact='lesson_draft', sequence=0,
        template_name='lesson_writer_prompt.j2', response_model=WriterArtifact,
        prompt_context={}, max_tokens=100,
        normalize_response=lambda raw: complete_writer_inventories(raw, CATALOG))
    assert result.lessons[0].source_ref_ids == [SOURCE['id']]
    assert len(calls) == 1
