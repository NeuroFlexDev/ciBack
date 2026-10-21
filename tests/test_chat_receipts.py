import pytest
from app.models.chat import ChatMessage
from app.models.user import User
from app.core.security import create_access_token


def test_same_chat_request_calls_provider_once_and_keeps_one_question(client, db_session, auth_headers, monkeypatch):
    chat = client.post('/api/chat/', headers=auth_headers, json={'name': 'Receipt test'}).json()
    calls = []
    def invoke(role, messages, **kwargs):
        calls.append(messages)
        assert len([m for m in messages if m.get('content') == 'Remember this']) == 1
        return {'text': 'Remembered', 'model': 'test', 'usage': {'input_tokens': 2, 'output_tokens': 1}}
    monkeypatch.setattr('app.ai.gateway.invoke_messages', invoke)
    payload = {'chat_id': chat['id'], 'text': 'Remember this', 'request_id': 'lost-response-123'}
    first = client.post('/api/chat/send', headers=auth_headers, json=payload)
    second = client.post('/api/chat/send', headers=auth_headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1 and db_session.query(ChatMessage).filter_by(chat_id=chat['id']).count() == 2
    status = client.get('/api/chat/requests/lost-response-123', headers=auth_headers).json()
    assert status['status'] == 'completed' and status['chat_id'] == chat['id']
    assert client.post('/api/chat/send', headers=auth_headers, json={**payload, 'text': 'Changed'}).status_code == 409


def test_failed_chat_keeps_question_and_retries_require_new_key(client, db_session, auth_headers, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('Too long')
    monkeypatch.setattr('app.services.chat_receipts.chat_generate', fail)
    payload = {'text': 'Never lose this draft', 'request_id': 'persist-failed-123'}
    assert client.post('/api/chat/send', headers=auth_headers, json=payload).status_code == 422
    receipt = client.get('/api/chat/requests/persist-failed-123', headers=auth_headers).json()
    assert receipt['status'] == 'failed' and receipt['text'] == payload['text']
    assert client.post('/api/chat/send', headers=auth_headers, json=payload).status_code == 409
    stranger = User(email='receipt-stranger@example.test', password_hash='unused')
    db_session.add(stranger); db_session.commit()
    assert client.get('/api/chat/requests/persist-failed-123', headers={'Authorization': 'Bearer '+create_access_token(stranger.id)}).status_code == 404
    assert db_session.query(ChatMessage).filter_by(content=payload['text']).count() == 1
