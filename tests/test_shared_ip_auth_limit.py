from collections import Counter
from fastapi import FastAPI, Request, Form
from fastapi.testclient import TestClient
from redis import RedisError
import pytest
from app.core import http_security as security
from app.core.security import create_access_token
from app.schemas.auth import AuthCredentials
from app.routes.account import PasswordChange, ResetConfirm
from app.routes.mfa import ChallengeInput


@pytest.fixture
def boundary(monkeypatch):
    counts = Counter()
    class RedisBoundary:
        def eval(self, script, number, *keys):
            assert number == len(keys)
            for key in keys:
                counts[key] += 1
            return [counts[key] for key in keys]
        def close(self): pass
    monkeypatch.setattr(security.Redis, 'from_url', lambda *args, **kwargs: RedisBoundary())
    monkeypatch.setattr(security.settings, 'ENV', 'stage')
    monkeypatch.setattr(security.settings, 'AUTH_RATE_LIMIT_ENABLED', True)
    monkeypatch.setattr(security.settings, 'AUTH_RATE_LIMIT_PER_MINUTE', 20)
    monkeypatch.setattr(security.settings, 'AUTH_RATE_LIMIT_PER_IP_MINUTE', 1800)
    app = FastAPI()
    app.add_middleware(security.SecurityMiddleware)
    @app.post('/api/auth/login')
    async def login(payload: AuthCredentials):
        # The downstream route must still receive the original body.
        return payload.model_dump()
    @app.post('/api/change-password')
    def change_password(payload: PasswordChange): return payload.model_dump()
    @app.post('/api/auth/password-reset/confirm')
    def reset_password(payload: ResetConfirm): return {'parsed': True}
    @app.post('/api/auth/mfa/verify')
    def mfa(payload: ChallengeInput): return {'parsed': True}
    @app.post('/api/lti/1/launch')
    def lti(state: str = Form(), id_token: str = Form()):
        return {'state': state, 'token_length': len(id_token)}
    @app.get('/api/auth/oidc/1/start')
    def sso(): return {'ready': True}
    return app, counts


def test_300_classroom_accounts_and_sso_starts_share_one_public_address(boundary):
    app, counts = boundary
    with TestClient(app) as client:
        for number in range(300):
            body = {'email': f'learner-{number}@example.test', 'password': 'test-only-password'}
            response = client.post('/api/auth/login', json=body)
            assert response.status_code == 200 and response.json() == body
            assert client.get('/api/auth/oidc/1/start').status_code == 200
    assert max(counts.values()) == 600
    assert all('@example.test' not in key and 'password' not in key for key in counts)


def test_account_limit_is_case_insensitive_and_survives_ip_changes(boundary):
    app, _ = boundary
    for index in range(21):
        with TestClient(app, client=(f'192.0.2.{index+1}', 51000)) as client:
            response = client.post('/api/auth/login', json={'email': ' Person@EXAMPLE.test ' if index % 2 else 'person@example.test', 'password': 'test-only-password'})
            assert response.status_code == (429 if index == 20 else 200)
    assert response.headers['Retry-After'] == '60'
    with TestClient(app) as client:
        assert client.post('/api/auth/login', json={'email': 'other@example.test', 'password': 'test-only-password'}).status_code == 200


def test_forwarded_header_cannot_reset_asgi_peer_ip_limit(boundary, monkeypatch):
    app, _ = boundary
    monkeypatch.setattr(security.settings, 'AUTH_RATE_LIMIT_PER_IP_MINUTE', 20)
    with TestClient(app) as client:
        for index in range(21):
            response = client.post('/api/auth/login', json={'email': f'{index}@example.test', 'password': 'test-only-password'},
                                  headers={'X-Forwarded-For': f'192.0.2.{index+1}'})
            assert response.status_code == (429 if index == 20 else 200)


def test_redis_outage_fails_closed_with_a_retryable_response(boundary, monkeypatch):
    app, _ = boundary
    def unavailable(*args, **kwargs): raise RedisError('test outage')
    monkeypatch.setattr(security.Redis, 'from_url', unavailable)
    with TestClient(app) as client:
        response = client.post('/api/auth/login', json={'email': 'one@example.test'})
    assert response.status_code == 503
    assert response.headers['X-Request-ID'] == response.json()['request_id']
    assert 'test outage' not in response.text


@pytest.mark.parametrize('media', ['Application/JSON', 'application/vnd.example+json', 'APPLICATION/PROBLEM+JSON; charset=utf-8', None])
def test_json_variants_cannot_bypass_actual_login_identity(boundary, media):
    app, counts = boundary
    with TestClient(app) as client:
        for index in range(21):
            response = client.post('/api/auth/login', content='{"email":"same@example.test","password":"test-only-password"}', headers={'Content-Type': media} if media else {})
            expected = 429 if index == 20 else (422 if media is None else 200)
            assert response.status_code == expected, response.text
    assert counts[security.private_key('account', 'same@example.test')] == 21


def test_missing_content_type_is_limited_when_fastapi_compatibility_mode_accepts_it(boundary):
    _, counts = boundary
    app = FastAPI(strict_content_type=False)
    app.add_middleware(security.SecurityMiddleware)
    @app.post('/api/auth/login')
    def login(payload: AuthCredentials): return payload.model_dump()
    with TestClient(app) as client:
        for index in range(21):
            response = client.post('/api/auth/login', content=b'{"email":"compat@example.test","password":"test-only-password"}')
            assert response.status_code == (429 if index == 20 else 200), response.text
    assert counts[security.private_key('account', 'compat@example.test')] == 21


def test_authenticated_password_changes_ignore_extra_email_and_rotating_tokens(boundary):
    app, counts = boundary
    with TestClient(app) as client:
        for index in range(21):
            token = create_access_token(123, amr=[f'fixture-{index}'])
            response = client.post('/api/change-password', headers={'Authorization': 'bEaReR ' + token},
                json={'old_password': 'wrong-but-valid-format', 'new_password': 'new-test-only-password', 'email': f'ignored-{index}@example.test'})
            assert response.status_code == (429 if index == 20 else 200), response.text
    assert counts[security.private_key('principal', '123')] == 21
    assert not any(':account:' in key for key in counts)


def test_reset_and_mfa_use_their_actual_one_time_credential(boundary):
    app, counts = boundary
    with TestClient(app) as client:
        for index in range(21):
            response = client.post('/api/auth/password-reset/confirm', json={'token': 'r' * 32, 'new_password': 'new-test-only-password', 'email': f'ignored-{index}@example.test'})
            assert response.status_code == (429 if index == 20 else 200)
            mfa = client.post('/api/auth/mfa/verify', json={'challenge_token': 'm' * 32, 'code': str(100000 + index)})
            assert mfa.status_code == (429 if index == 20 else 200)
    assert not any(':account:' in key for key in counts)


def test_json_credentials_are_bounded_with_and_without_content_length(boundary):
    app, _ = boundary
    payload = b'{"email":"one@example.test","password":"' + b'a' * security.MAX_AUTH_JSON_BYTES + b'"}'
    with TestClient(app) as client:
        for content in (payload, iter([payload[:100], payload[100:]])):
            response = client.post('/api/auth/login', content=content, headers={'Content-Type': 'application/json'})
            assert response.status_code == 413 and response.headers['X-Request-ID']
        # Replaying a streamed valid body still reaches the actual schema.
        response = client.post('/api/auth/login', content=iter([b'{"email":"one@example.test",', b'"password":"test-only-password"}']), headers={'Content-Type': 'application/json'})
        assert response.status_code == 200 and response.json()['email'] == 'one@example.test'


def test_lti_form_post_is_not_consumed_or_subject_to_json_size_cap(boundary):
    app, _ = boundary
    with TestClient(app) as client:
        response = client.post('/api/lti/1/launch', data={'state': 's' * 64, 'id_token': 't' * 32768})
    assert response.status_code == 200 and response.json() == {'state': 's' * 64, 'token_length': 32768}


def test_ip_ceiling_is_checked_before_reading_the_body(boundary, monkeypatch):
    app, _ = boundary
    monkeypatch.setattr(security.settings, 'AUTH_RATE_LIMIT_PER_IP_MINUTE', 1)
    with TestClient(app) as client:
        assert client.post('/api/auth/login', json={'email': 'one@example.test', 'password': 'test-only-password'}).status_code == 200
        async def must_not_read(_): raise AssertionError('IP-limited requests must not read a body')
        monkeypatch.setattr(security, 'account_bucket', must_not_read)
        assert client.post('/api/auth/login', content=b'x' * 40000).status_code == 429
