import json
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import Mock
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import SecretStr
from app.ai import control, providers, routing, gateway, managed_http
from app.core.config import settings
from app.core.integration_config import integration_settings
from app.models.ai_control import AIControl
from app.models.administration import AdminEvent
from app.services import ai_control_service as service


@pytest.fixture
def managed(db_session, auth_user, monkeypatch, isolated_ai_control):
    auth_user.is_admin = True
    db_session.add(AIControl(id=1, revision=0, providers=[], candidates=[], policy={}))
    db_session.commit()
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(settings, 'AI_ROUTING_MODE', 'adaptive')
    def read():
        db_session.expire_all()
        row = db_session.get(AIControl, 1)
        return deepcopy({"revision": row.revision, "providers": row.providers, "candidates": row.candidates, "policy": row.policy})
    monkeypatch.setattr(control, 'read_snapshot', read)
    monkeypatch.setattr(managed_http.socket, 'getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))])
    control.invalidate()
    return auth_user


def provider_payload(revision=0, **kwargs):
    return {"expected_revision": revision, "name": "Test cloud", "base_url": "https://models.example/v1",
            "enabled": True, "key_action": "replace", "api_key": "secret-test-provider-key", **kwargs}


def model_payload(revision=1, **kwargs):
    return {"expected_revision": revision, "provider": "cloud", "model": "writer-small", "roles": ["lesson_writer"],
            "approved": True, "input_rub_per_million": "1.5", "output_rub_per_million": "3",
            "max_input_tokens": 64000, "max_output_tokens": 8000, "context_window": 64000, **kwargs}


def test_operator_only_and_no_input_reflection(client, auth_headers, auth_user):
    assert client.get('/api/admin/ai', headers=auth_headers).status_code == 403
    auth_user.is_admin = True
    response = client.put('/api/admin/ai/providers/cloud', headers=auth_headers,
                          json=provider_payload(timeout_seconds='secret-test-provider-key', api_key={'password': 'secret-test-provider-key'}))
    assert response.status_code == 422
    assert 'secret-test-provider-key' not in response.text


def test_encrypted_key_not_in_read_api_or_audit_and_live_routing(client, auth_headers, managed, db_session):
    response = client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload())
    assert response.status_code == 200, response.text
    row = db_session.get(AIControl, 1)
    assert 'secret-test-provider-key' not in json.dumps(row.providers)
    assert providers.credential(providers.registry()['cloud']) == 'secret-test-provider-key'
    read = client.get('/api/admin/ai', headers=auth_headers)
    assert read.status_code == 200
    assert 'secret-test-provider-key' not in read.text and 'sealed_key' not in read.text and 'api_key_env' not in read.text
    before = routing.policy_fingerprint()
    response = client.put('/api/admin/ai/models', headers=auth_headers, json=model_payload())
    assert response.status_code == 200, response.text
    route = routing.choose_route(role='lesson_writer', baseline_model=settings.AI_MODEL_WRITER,
        input_tokens=1000, output_tokens=300, available_providers={'cloud'})
    assert (route.selected_provider, route.selected_model) == ('cloud', 'writer-small')
    assert routing.policy_fingerprint() != before
    assert all('secret-test-provider-key' not in json.dumps(event.details) for event in db_session.query(AdminEvent).all())


def test_rotation_removal_endpoint_binding_and_conflict(client, auth_headers, managed):
    assert client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload()).status_code == 200
    changed = provider_payload(1, base_url='https://other.example/v1', key_action='keep')
    assert client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=changed).status_code == 422
    assert client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload(0)).status_code == 409
    assert providers.credential(providers.registry()['cloud']) == 'secret-test-provider-key'
    assert client.put('/api/admin/ai/providers/cloud', headers=auth_headers,
        json=provider_payload(1, api_key='rotated-key')).status_code == 200
    assert providers.credential(providers.registry()['cloud']) == 'rotated-key'
    assert client.put('/api/admin/ai/providers/cloud', headers=auth_headers,
        json=provider_payload(2, enabled=False, key_action='remove')).status_code == 200
    assert not providers.credential(providers.registry()['cloud'])
    assert not providers.available(providers.registry()['cloud'])


def test_environment_key_not_reused_at_new_endpoint(client, auth_headers, managed, monkeypatch):
    monkeypatch.setattr(settings, 'VSELLM_API_KEY', SecretStr('environment-private-key'))
    assert client.put('/api/admin/ai/providers/vsellm', headers=auth_headers,
        json=provider_payload(0, key_action='keep')).status_code == 422
    assert client.put('/api/admin/ai/providers/vsellm', headers=auth_headers,
        json=provider_payload(0, base_url=settings.VSELLM_BASE_URL, key_action='keep')).status_code == 200
    assert providers.credential(providers.registry()['vsellm']) == 'environment-private-key'
    assert client.put('/api/admin/ai/providers/vsellm', headers=auth_headers,
        json=provider_payload(1, base_url=settings.VSELLM_BASE_URL, key_action='remove', enabled=False)).status_code == 200
    assert providers.credential(providers.registry()['vsellm']) == ''


@pytest.mark.parametrize('extra', [
    {'roles': ['fake']}, {'context_window': 100}, {'roles': ['critic_qa']}, {'roles': ['vision']},
    {'input_rub_per_million': '0'}, {'output_rub_per_million': 'NaN'}, {'extra_body': {'messages': []}},
])
def test_model_validation(client, auth_headers, managed, extra):
    client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload())
    assert client.put('/api/admin/ai/models', headers=auth_headers, json=model_payload(**extra)).status_code == 422


def test_baseline_revocation_is_enforced_at_gateway(client, auth_headers, managed, monkeypatch):
    monkeypatch.setattr(settings, 'VSELLM_API_KEY', SecretStr('baseline-key'))
    model = model_payload(0, provider='vsellm', model=settings.AI_MODEL_WRITER, approved=False)
    assert client.put('/api/admin/ai/models', headers=auth_headers, json=model).status_code == 200
    sent = Mock(side_effect=AssertionError('revoked route reached transport'))
    monkeypatch.setattr(gateway, 'ChatOpenAI', sent)
    with pytest.raises(providers.AIUnavailable):
        gateway.invoke_messages('lesson_writer', [{'role': 'user', 'content': 'Hello'}], max_tokens=100, provider='vsellm')
    sent.assert_not_called()


def test_shared_refresh_failure_does_not_restore_environment_key(managed, monkeypatch):
    monkeypatch.setattr(settings, 'VSELLM_API_KEY', SecretStr('baseline-key'))
    assert providers.configured(providers.registry()['vsellm'])
    monkeypatch.setattr(control, 'read_snapshot', Mock(side_effect=RuntimeError('db down')))
    control.invalidate()
    assert providers.registry() == {}
    assert not gateway.configured()


def test_local_mode_policy_blocks_cloud_and_web_search(client, auth_headers, managed):
    client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload())
    assert providers.available(providers.registry()['cloud'])
    response = client.put('/api/admin/ai/policy', headers=auth_headers,
        json={'expected_revision': 1, 'mode': 'adaptive', 'local_only': True, 'min_samples': 10, 'min_pass_rate': .85})
    assert response.status_code == 200
    assert not providers.available(providers.registry()['cloud'])
    from app.ai import web_search
    assert not web_search.configured()


def test_ssrf_and_local_allowlist(monkeypatch):
    monkeypatch.setattr(managed_http.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError): managed_http.destination('https://models.example/v1')
    with pytest.raises(ValueError): managed_http.destination('http://localhost:11434/v1', local=True)
    monkeypatch.setattr(settings, 'AI_LOCAL_ENDPOINTS', ['http://localhost:11434'])
    assert managed_http.destination('http://localhost:11434/v1', local=True)[2] == 11434
    with pytest.raises(ValueError): managed_http.destination('http://localhost:11435/v1', local=True)
    with pytest.raises(ValueError): managed_http.destination('https://user:secret@models.example/v1')


def test_probe_sends_no_course_content_and_handles_provider_errors(client, auth_headers, managed, monkeypatch):
    client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload())
    sent = []
    def handle(request):
        sent.append(request)
        return httpx.Response(403, json={'error': 'secret-test-provider-key'})
    @contextmanager
    def fake_client(provider):
        with httpx.Client(transport=httpx.MockTransport(handle)) as value:
            yield value
    monkeypatch.setattr(service, 'http_client', fake_client)
    response = client.post('/api/admin/ai/providers/cloud/check', headers=auth_headers, json={'expected_revision': 1})
    assert response.status_code == 200 and response.json()['status'] == 'access_denied'
    assert 'secret-test-provider-key' not in response.text
    assert len(sent) == 1 and sent[0].method == 'GET' and sent[0].content == b''
    assert str(sent[0].url) == 'https://models.example/v1/models'


def test_transport_rejects_cross_origin_before_network(monkeypatch):
    from app.core.config import AIProvider
    provider = AIProvider(id='cloud', base_url='https://models.example/v1')
    connection = Mock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr(managed_http.socket, 'create_connection', connection)
    with managed_http.client(provider) as client:
        with pytest.raises(httpx.RequestError): client.get('https://other.example/v1/models')
    connection.assert_not_called()


def test_real_sdk_uses_pinned_local_transport_and_project_header(monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from langchain_openai import ChatOpenAI
    from app.core.config import AIProvider
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body = json.dumps({'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'local-test',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"ok":true}'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args): pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    address = f'http://127.0.0.1:{server.server_port}'
    thread = Thread(target=server.handle_request, daemon=True); thread.start()
    monkeypatch.setattr(settings, 'AI_LOCAL_ENDPOINTS', [address])
    provider = AIProvider(id='local', base_url=address + '/v1', local=True, project='test-project')
    provider._managed = True
    try:
        options = providers.client_options(provider)
        with options['http_client']:
            result = ChatOpenAI(model='local-test', api_key='test-key', base_url=str(provider.base_url),
                max_tokens=20, max_retries=0, temperature=None, **options).invoke('test')
        assert result.content == '{"ok":true}'
        assert result.usage_metadata['output_tokens'] == 4
        assert received[0][0] == '/v1/chat/completions'
        assert {k.lower(): v for k, v in received[0][1].items()}['openai-project'] == 'test-project'
        assert received[0][2]['model'] == 'local-test'
    finally:
        server.server_close(); thread.join(timeout=2)


def test_other_process_changes_are_loaded_after_ttl(managed, db_session, monkeypatch):
    from types import SimpleNamespace
    ticks = [100.0]
    monkeypatch.setattr(control, 'time', SimpleNamespace(monotonic=lambda: ticks[0]))
    control.invalidate()
    assert not control.policy()['local_only']
    row = db_session.get(AIControl, 1)
    row.policy = {'local_only': True}; row.revision = 1; db_session.commit()
    assert not control.policy()['local_only']
    ticks[0] += 5.1
    assert control.policy()['local_only']


def test_long_model_id_fits_audit_storage(client, auth_headers, managed, db_session):
    client.put('/api/admin/ai/providers/cloud', headers=auth_headers, json=provider_payload())
    model_id = 'publisher/' + 'model-name-' * 15
    assert client.put('/api/admin/ai/models', headers=auth_headers, json=model_payload(model=model_id)).status_code == 200
    event = db_session.query(AdminEvent).filter_by(action='ai.model.updated').one()
    assert len(event.target_id) <= 64 and event.details['reference'] == 'cloud::' + model_id


def test_installation_local_only_policy_cannot_be_overridden(client, auth_headers, managed, monkeypatch):
    monkeypatch.setattr(settings, 'AI_LOCAL_ONLY', True)
    response = client.put('/api/admin/ai/policy', headers=auth_headers,
        json={'expected_revision': 0, 'mode': 'adaptive', 'local_only': False})
    assert response.status_code == 422
    assert client.get('/api/admin/ai', headers=auth_headers).json()['policy']['local_only']
    assert control.policy()['local_only']
