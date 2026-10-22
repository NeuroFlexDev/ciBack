import time
from contextlib import ExitStack
import jwt
import pytest
from pydantic import SecretStr
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from app.application import create_app, operator_route
from app.core.config import settings
from app.core.security import create_access_token
from app.core.surface import surface
from app.database.db import get_db
from app.models.mfa import MFAChallenge
from app.services.mfa_service import digest, now
from datetime import timedelta


@pytest.fixture
def surfaces(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_JWT_SECRET', SecretStr('admin-test-key-independent-1234567890123456789'))
    monkeypatch.setattr(settings, 'AUTH_RATE_LIMIT_ENABLED', False)
    apps = {name: create_app(name) for name in ('admin', 'platform')}
    for app in apps.values():
        app.dependency_overrides[get_db] = lambda: db_session
    with ExitStack() as stack:
        yield {name: stack.enter_context(TestClient(app)) for name, app in apps.items()}


def bearer(token):
    return {'Authorization': 'Bearer ' + token}


def test_operator_routes_absent_from_platform_and_no_tenant_api_in_admin(surfaces):
    platform = surfaces['platform']
    admin = surfaces['admin']
    paths = platform.get("/openapi.json").json()["paths"]
    assert "/api/auth/login" in paths
    assert not any(path.startswith("/api/admin/") for path in paths)
    for path in ('/api/admin/organizations', '/api/admin/operations', '/api/admin/ai', '/api/support/grants/1/content'):
        assert platform.get(path).status_code == 404
        assert path not in platform.get('/openapi.json').json()['paths']
    assert admin.get('/api/courses').status_code == 404
    assert admin.get('/api/business/positions').status_code == 404
    assert admin.post('/api/auth/register', json={}).status_code == 404
    assert admin.post('/api/auth/invitations/accept', json={}).status_code == 404
    assert admin.post('/api/support/grants/1/decision', json={}).status_code == 404


def test_only_operator_login_and_cross_service_tokens_rejected(surfaces, auth_user, db_session):
    platform, admin = surfaces['platform'], surfaces['admin']
    payload = {'email': auth_user.email, 'password': 'password123'}
    assert admin.post('/api/auth/login', json=payload).status_code == 401
    auth_user.is_admin = True
    db_session.commit()
    public_token = platform.post('/api/auth/login', json=payload).json()['access_token']
    admin_response = admin.post('/api/auth/login', json=payload)
    assert admin_response.status_code == 200, admin_response.text
    admin_token = admin_response.json()['access_token']
    assert admin.get('/api/auth/me', headers=bearer(public_token)).status_code == 401
    assert platform.get('/api/auth/me', headers=bearer(admin_token)).status_code == 401
    assert admin.get('/api/auth/me', headers=bearer(admin_token)).status_code == 200
    assert admin.get('/api/admin/organizations', headers=bearer(admin_token)).status_code == 200
    # Even a token signed with the admin key cannot cross with the wrong audience.
    payload = jwt.decode(admin_token, options={'verify_signature': False})
    payload['aud'] = 'lernium:platform'
    wrong_audience = jwt.encode(payload, settings.ADMIN_JWT_SECRET.get_secret_value(), algorithm='HS256')
    assert admin.get('/api/auth/me', headers=bearer(wrong_audience)).status_code == 401
    auth_user.is_admin = False
    db_session.commit()
    assert admin.get('/api/auth/me', headers=bearer(admin_token)).status_code == 401


def test_mfa_challenge_cannot_cross_surfaces(surfaces, auth_user, db_session):
    raw = 'a' * 40
    auth_user.is_admin = True
    challenge = MFAChallenge(token_hash=digest(raw), user_id=auth_user.id, auth_version=0, primary_method='pwd',
                             context={'surface': 'platform'}, expires_at=now() + timedelta(minutes=5))
    db_session.add(challenge); db_session.commit()
    response = surfaces['admin'].post('/api/auth/mfa/verify', json={'challenge_token': raw, 'code': '123456'})
    assert response.status_code == 400
    assert 'другому сервису' in response.text
    db_session.refresh(challenge)
    assert challenge.consumed_at is None and challenge.attempts == 0


def test_admin_requires_independent_key(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_JWT_SECRET', settings.JWT_SECRET)
    with pytest.raises(RuntimeError, match='separate ADMIN_JWT_SECRET'):
        create_app('admin')


def test_admin_mfa_login_issues_admin_session(surfaces, auth_user, db_session, monkeypatch):
    from cryptography.fernet import Fernet
    from app.core.integration_config import integration_settings
    from app.services import mfa_service
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    auth_user.is_admin = True
    db_session.commit()
    admin, platform = surfaces['admin'], surfaces['platform']
    login = {'email': auth_user.email, 'password': 'password123'}
    token = admin.post('/api/auth/login', json=login).json()['access_token']
    setup = admin.post('/api/auth/mfa/enroll', headers=bearer(token), json={'password': 'password123'}).json()
    confirmed = admin.post('/api/auth/mfa/enroll/confirm', headers=bearer(token), json={
        'enrollment_token': setup['enrollment_token'], 'code': mfa_service.otp(setup['secret'], int(time.time()) // 30)})
    assert confirmed.status_code == 200, confirmed.text
    challenge = admin.post('/api/auth/login', json=login).json()
    proof = {'challenge_token': challenge['challenge_token'], 'code': confirmed.json()['recovery_codes'][0]}
    assert platform.post('/api/auth/mfa/verify', json=proof).status_code == 400
    result = admin.post('/api/auth/mfa/verify', json=proof)
    assert result.status_code == 200, result.text
    access = result.json()['access_token']
    assert admin.get('/api/admin/organizations', headers=bearer(access)).status_code == 200
    assert platform.get('/api/auth/me', headers=bearer(access)).status_code == 401
