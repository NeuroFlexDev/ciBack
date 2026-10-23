import json
from decimal import Decimal
import pytest
from fastapi import HTTPException
from app.services.assistant_context import build_context, local_answer, resolve_references, verify_context
from app.services.ai_access_boundary import principal_stamp
from app.services import learner_assistant_service as learner, ai_resources
from app.models.theory import Theory
from app.core.config import settings
from tests.test_learning_runtime import world, publish, assign
from tests.test_ai_runtime_v2 import SOURCE


def test_library_contains_author_text_but_no_foreign_drafts(client, db_session, world):
    own, _, lesson, *_ = publish(client, db_session, world['author'], name='Безопасность')
    foreign, *_ = publish(client, db_session, world['foreign'], name='Foreign secret')
    theory = db_session.query(Theory).filter_by(lesson_id=lesson.id).one()
    theory.content = 'Давление не выше 8 бар.'; db_session.commit()
    context = build_context(db_session, world['author'], 'Какое давление?')
    prompt = context.prompt()
    assert '8 бар' in prompt and 'Foreign secret' not in prompt
    answer = local_answer('давление', context)
    assert answer['degraded'] and '8 бар' in answer['text'] and f'/courses/{own.id}/edit' in answer['text']
    with pytest.raises(ValueError):
        resolve_references('[course:999999]', context)
    with pytest.raises(HTTPException):
        build_context(db_session, world['author'], 'secret', course_id=foreign.id)
    stamp = principal_stamp(world['author'])
    own.is_deleted = True; db_session.commit()
    with pytest.raises(HTTPException):
        verify_context(db_session, world['author'].id, stamp, context)


def test_learner_context_uses_assigned_version_without_keys(client, db_session, world):
    course, _, lesson, *_ = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    theory = db_session.query(Theory).filter_by(lesson_id=lesson.id).one()
    theory.content = 'Unpublished confidential draft'; db_session.commit()
    context = build_context(db_session, world['learner'], 'published lesson')
    prompt = context.prompt().lower()
    assert 'original published lesson' in prompt
    assert 'confidential' not in prompt and 'correct_answer' not in prompt
    assert f'assigned:{assignment["id"]}:{lesson.id}' in prompt


def test_learner_outage_returns_grounded_local_answer(client, db_session, world, monkeypatch):
    course, _, lesson, *_ = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    monkeypatch.setattr(ai_resources, 'completion', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline')))
    result = learner.send_message(db_session, world['learner'], assignment['id'], lesson.id, 'published lesson?', 'offline-request')
    assert result.status == 'completed' and result.model == 'local/extractive'
    assert 'Original published lesson' in result.answer and result.citations[0]['lesson_id'] == lesson.id


def test_unlimited_is_metered_and_keeps_capacity_but_not_financial_limits(db_session, world, monkeypatch):
    org = world['org']; org.ai_unlimited = True; db_session.commit()
    monkeypatch.setattr(settings, 'AI_INSTALL_MONTHLY_TOKENS', 1)
    monkeypatch.setattr(settings, 'AI_ORG_MONTHLY_TOKENS', 1)
    monkeypatch.setattr(settings, 'AI_ORG_MONTHLY_RUB', Decimal('0'))
    record = ai_resources.reserve(db_session, owner_id=world['owner'].id, operation='chat', tokens=100, cost=Decimal('10'))
    db_session.commit(); ai_resources.finish(db_session, record, usage={'input_tokens':100})
    usage = ai_resources.usage_summary(db_session, world['owner'].id)
    assert usage['ai_unlimited'] and usage['tokens_reserved_or_used'] == 100
    # The exempt organization's consumption cannot exhaust other tenants' pool.
    ai_resources.reserve(db_session, owner_id=world['foreign'].id, operation='chat', tokens=1)
    db_session.commit()
    monkeypatch.setattr(settings, 'AI_ORG_INTERACTIVE_LIMIT', 0)
    with pytest.raises(ai_resources.CapacityBusy):
        ai_resources.reserve(db_session, owner_id=world['owner'].id, operation='chat', lane='interactive')


def test_ingestion_derives_indexes_accepts_numeric_lists_and_rejects_fiction():
    from app.ai.evidence import complete_ingestion_inventories
    from app.services.agent_runtime import expand_source_refs
    from app.schemas.agentic_pipeline import IngestionArtifact
    raw = {'knowledge_items': [{'id':'kn:pressure', 'kind':'requirement', 'title':'Давление',
        'statement':'8 бар', 'source_ref_ids':[SOURCE['id']], 'attributes': {'limits':[1, 8, 10.5]}}]}
    normalized = complete_ingestion_inventories(raw, [SOURCE])
    artifact = IngestionArtifact.model_validate(expand_source_refs(normalized, {SOURCE['id']:SOURCE}))
    assert artifact.documents[0].knowledge_item_ids == ['kn:pressure']
    assert artifact.knowledge_items[0].attributes['limits'] == [1, 8, 10.5]
    assert 'documents' not in raw
    raw['knowledge_items'][0]['source_ref_ids'] = ['src:invented']
    with pytest.raises(ValueError, match='Unknown'):
        complete_ingestion_inventories(raw, [SOURCE])
