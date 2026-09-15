import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.ai import memory, retrieval
from app.core.config import settings
from app.models.ai_state import ChatMemory, ChunkEmbedding
from app.models.chat import Chat, ChatMessage
from app.services.retrieval_service import RetrievalService
from tests.factories import make_course
from tests.test_acl_retrieval import _document


def test_workspace_persists_and_rejects_stale_edits(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    path = f'/api/courses/{course.id}/workspace'
    snapshot = {'nodes': [], 'edges': []}
    assert client.get(path, headers=auth_headers).json()['version'] == 0
    assert client.put(path, headers=auth_headers, json={'version':0, 'snapshot':snapshot}).json()['version'] == 1
    assert client.put(path, headers=auth_headers, json={'version':0, 'snapshot':snapshot}).status_code == 409
    db_session.expire_all()
    saved = client.get(path, headers=auth_headers).json()
    assert saved['snapshot']['courseId'] == str(course.id)
    assert saved['version'] == 1
    foreign = make_course(db_session)
    assert client.get(f'/api/courses/{foreign.id}/workspace', headers=auth_headers).status_code == 404
    assert client.put(f'/api/courses/{foreign.id}/workspace', headers=auth_headers,
                      json={'version':0, 'snapshot':snapshot}).status_code == 404


@pytest.mark.parametrize('snapshot', [
    {'nodes': [], 'edges': [1]},
    {'nodes': [{'id':'a'}], 'edges': []},
    {'nodes': [], 'edges': [], 'viewport': {'x':0,'y':0,'zoom':0}},
    {'nodes': [{'id':'a', 'position':{'x':0,'y':0}, 'data':{'nodeType':'lesson','title':'a'},'parentId':'a'}], 'edges': []},
])
def test_canvas_rejects_malformed_or_cyclic_input(client, db_session, auth_user, auth_headers, snapshot):
    course = make_course(db_session, owner_id=auth_user.id)
    assert client.put(f'/api/courses/{course.id}/workspace', headers=auth_headers,
                      json={'version':0,'snapshot':snapshot}).status_code == 422


def test_embeddings_reused_and_retrievable_after_store_recreation(db_session, auth_user, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    _, chunk = _document(db_session, course, auth_user.id, 'safety.txt')
    chunk.text = 'Остановить установку при утечке'
    calls = []
    class Embedder:
        def embed_documents(self, texts):
            calls.append(texts)
            return [[1.0, 0.0] for _ in texts]
        def embed_query(self, query):
            return [1.0, 0.0]
    monkeypatch.setattr(retrieval, 'embedding_client', lambda: Embedder())
    monkeypatch.setattr(retrieval.gateway, 'configured', lambda: True)
    retrieval.persist_embeddings(db_session, [chunk]); db_session.commit()
    retrieval.persist_embeddings(db_session, [chunk]); db_session.commit()
    assert len(calls) == 1
    assert db_session.query(ChunkEmbedding).count() == 1
    db_session.expire_all()
    result = RetrievalService.search_course(db_session, course_id=course.id, owner_id=auth_user.id,
        query='утечка', limit=3, vector_store=retrieval.PersistentVectorStore(db_session))
    assert [item.chunk_id for item in result.citations] == [chunk.id]
    course.is_deleted = True; db_session.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        RetrievalService.search_course(db_session, course_id=course.id, owner_id=auth_user.id,
            query='утечка', limit=3, vector_store=retrieval.PersistentVectorStore(db_session))
    assert exc.value.status_code == 404


def test_long_conversation_compacts_before_count_limit_and_is_owner_scoped(db_session, auth_user, monkeypatch):
    chat = Chat(owner_id=auth_user.id, title='Memory')
    db_session.add(chat); db_session.flush()
    db_session.add_all([ChatMessage(chat_id=chat.id, role='user' if i % 2 == 0 else 'assistant',
        content=f'message {i}: ' + 'constraint ' * 300) for i in range(4)])
    db_session.commit()
    calls = []
    monkeypatch.setattr(memory, 'chat_completion', lambda role, messages, **kw: calls.append(messages) or {'text':'Explicit user constraint retained'})
    context = memory.conversation_context(db_session, chat.id, auth_user.id, 'Continue')
    assert calls and len(context) < 5
    row = db_session.get(ChatMemory, chat.id)
    assert row.owner_id == auth_user.id
    db_session.expire_all()
    assert memory.conversation_context(db_session, chat.id, auth_user.id, 'Continue')[0]['content'].endswith('Explicit user constraint retained')
    assert len(calls) == 1
    with pytest.raises(KeyError):
        memory.conversation_context(db_session, chat.id, auth_user.id + 999, 'Secret?')


def test_real_sqlite_checkpoint_recovers_without_repeating_completed_node(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CHECKPOINT_SQLITE_PATH', str(tmp_path/'checkpoints.sqlite'))
    script = Path(__file__).resolve().parents[1]/'scripts/check_ai_persistence.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'passed' in result.stdout


def test_batch_merge_namespaces_generated_ids_and_preserves_targets():
    from app.ai.curriculum import merge_assessments
    from app.schemas.agentic_pipeline import AssessmentArtifact
    from tests.test_ai_runtime_v2 import SOURCE
    part = AssessmentArtifact.model_validate({'course_plan_id':'plan:safety','source_refs':[SOURCE],
        'module_ids':['mod:safety'],'lesson_ids':['lesson:safety'],'objective_ids':['obj:safety'],
        'competency_ids':['cmp:safety'],'questions':[{'id':'question:check','kind':'short_answer',
        'scope':'module','target_id':'mod:safety','prompt':'What is the limit?', 'expected_answer':'8 bar',
        'explanation':'Source requirement','objective_ids':['obj:safety'],'competency_ids':['cmp:safety'],
        'source_ref_ids':[SOURCE['id']]}]})
    merged = merge_assessments([part, part])
    assert len({q.id for q in merged.questions}) == 2
    assert {q.target_id for q in merged.questions} == {'mod:safety'}
