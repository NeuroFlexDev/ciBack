"""Security flows use real endpoints and durable SQL state, never a mock OTP."""
import base64
from datetime import timedelta
import secrets
import time

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.integration_config import integration_settings
from app.core.mfa_config import mfa_settings
from app.core.security import create_access_token, decode_access_token
from app.models.mfa import UserMFA, MFAChallenge, MFARecoveryCode
from app.services import mfa_service as svc


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(mfa_settings, 'MFA_REQUIRED_FOR_OPERATORS', False)


@pytest.fixture
def clock(monkeypatch):
    value = [int(time.time())]
    monkeypatch.setattr(svc.time, 'time', lambda: value[0])
    return value


def headers(result):
    return {'Authorization': 'Bearer ' + result['access_token']}


def login(client, user, password='password123'):
    return client.post('/api/auth/login', json={'email': user.email, 'password': password})


def enroll(client, user, clock):
    original = headers({'access_token': create_access_token(user.id, user.auth_version)})
    response = client.post('/api/auth/mfa/enroll', headers=original, json={'password': 'password123'})
    assert response.status_code == 200, response.text
    setup = response.json()
    result = client.post('/api/auth/mfa/enroll/confirm', headers=original,
        json={'enrollment_token': setup['enrollment_token'], 'code': svc.otp(setup['secret'], clock[0]//30)})
    assert result.status_code == 200, result.text
    return setup, result.json(), original


def test_rfc6238_vector():
    # RFC 6238 Appendix B SHA-1 value 89005924, truncated to six digits.
    key = base64.b32encode(b'12345678901234567890').decode()
    assert svc.otp(key, 1234567890//30) == '005924'


def test_pending_enrollment_is_encrypted_and_expires(client, auth_user, auth_headers, db_session, clock):
    assert client.get('/api/auth/mfa/status', headers=auth_headers).json()['enabled'] is False
    assert client.post('/api/auth/mfa/enroll', headers=auth_headers, json={'password': 'wrong'}).status_code == 400
    setup = client.post('/api/auth/mfa/enroll', headers=auth_headers, json={'password': 'password123'}).json()
    factor = db_session.get(UserMFA, auth_user.id)
    assert setup['secret'] not in factor.pending_secret_encrypted
    assert factor.enrollment_token_hash != setup['enrollment_token']
    assert 'access_token' in login(client, auth_user).json()  # Not enabled before proof.
    clock[0] += 601
    result = client.post('/api/auth/mfa/enroll/confirm', headers=auth_headers,
        json={'enrollment_token': setup['enrollment_token'], 'code': svc.otp(setup['secret'], clock[0]//30)})
    assert result.status_code == 400
    assert not factor.enabled


def test_login_challenge_is_not_a_session_and_revokes_legacy(client, auth_user, db_session, clock):
    setup, confirmed, old = enroll(client, auth_user, clock)
    assert client.get('/api/auth/me', headers=old).status_code == 401
    plain = headers({'access_token': create_access_token(auth_user.id, auth_user.auth_version)})
    assert client.get('/api/auth/me', headers=plain).status_code == 401
    assert client.get('/api/auth/me', headers=headers(confirmed)).status_code == 200
    challenge = login(client, auth_user).json()
    assert challenge['mfa_required'] and 'access_token' not in challenge
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer '+challenge['challenge_token']}).status_code == 401
    clock[0] += 31
    payload = {'challenge_token': challenge['challenge_token'], 'code': svc.otp(setup['secret'], clock[0]//30)}
    result = client.post('/api/auth/mfa/verify', json=payload)
    assert result.status_code == 200, result.text
    assert decode_access_token(result.json()['access_token'])['amr'] == ['pwd', 'otp']
    assert client.post('/api/auth/mfa/verify', json=payload).status_code == 400
    row = db_session.get(MFAChallenge, svc.digest(challenge['challenge_token']))
    assert row.consumed_at is not None

def test_missing_encryption_key_does_not_enable_or_reveal_a_factor(client, auth_user, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(''))
    status = client.get('/api/auth/mfa/status', headers=auth_headers).json()
    assert not status['enrollment_available'] and not status['enabled']
    result = client.post('/api/auth/mfa/enroll', headers=auth_headers, json={'password':'password123'})
    assert result.status_code == 503
    assert 'secret' not in result.json()
    assert not db_session.get(UserMFA, auth_user.id).enabled


def test_expired_material_purge_retains_active_factor(client, auth_user, db_session, clock):
    from scripts.purge_auth_challenges import run
    setup, confirmed, _ = enroll(client, auth_user, clock)
    login(client, auth_user)
    result = run(db_session, svc.now()+timedelta(days=2))
    db_session.commit()
    assert result['removed_old_challenges'] == 1
    factor = db_session.get(UserMFA, auth_user.id)
    assert factor.enabled and svc.secret_for(factor) == setup['secret']
    assert db_session.query(MFARecoveryCode).filter_by(user_id=auth_user.id).count() == 10
    assert client.get('/api/auth/mfa/status', headers=headers(confirmed)).headers['Cache-Control'] == 'no-store'


def test_password_reset_keeps_factor_and_revokes_incomplete_login(client, auth_user, db_session, clock):
    from app.models.account import PasswordReset
    _, confirmed, _ = enroll(client, auth_user, clock)
    challenge = login(client, auth_user).json()
    raw = secrets.token_urlsafe(32)
    db_session.add(PasswordReset(token_hash=svc.digest(raw),user_id=auth_user.id,expires_at=svc.now()+timedelta(minutes=30)))
    db_session.commit()
    result = client.post('/api/auth/password-reset/confirm',json={'token':raw,'new_password':'new-recovered-password'})
    assert result.status_code == 200
    assert db_session.get(UserMFA,auth_user.id).enabled
    assert client.post('/api/auth/mfa/verify',json={'challenge_token':challenge['challenge_token'],'code':confirmed['recovery_codes'][0]}).status_code == 400
    assert login(client,auth_user,'new-recovered-password').json()['mfa_required']


def test_mfa_migration_roundtrip():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('security27',Path(__file__).parents[1]/'migrations/versions/security_0027.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.down_revision == 'support_0026'
    engine=create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        context=MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
            assert {'user_mfa','mfa_challenges','mfa_recovery_codes'} <= set(inspect(connection).get_table_names())
            module.downgrade()
        assert inspect(connection).get_table_names() == ['users']
    engine.dispose()



def test_totp_and_recovery_are_single_use(client, auth_user, db_session, clock):
    setup, confirmed, _ = enroll(client, auth_user, clock)
    challenge = login(client, auth_user).json()
    payload = {'challenge_token': challenge['challenge_token'], 'code': svc.otp(setup['secret'], clock[0]//30)}
    assert client.post('/api/auth/mfa/verify', json=payload).status_code == 400
    assert db_session.get(MFAChallenge, svc.digest(challenge['challenge_token'])).attempts == 1
    payload['code'] = confirmed['recovery_codes'][0]
    assert client.post('/api/auth/mfa/verify', json=payload).status_code == 200
    payload['challenge_token'] = login(client, auth_user).json()['challenge_token']
    assert client.post('/api/auth/mfa/verify', json=payload).status_code == 400
    rows = db_session.query(MFARecoveryCode).filter_by(user_id=auth_user.id).all()
    assert len(rows) == 10 and sum(row.consumed_at is not None for row in rows) == 1
    assert all(row.code_hash not in confirmed['recovery_codes'] for row in rows)


def test_failed_guess_budget_survives_new_challenges(client, auth_user, db_session, clock):
    enroll(client, auth_user, clock)
    for _ in range(2):
        challenge = login(client, auth_user).json()
        for _ in range(5):
            assert client.post('/api/auth/mfa/verify', json={'challenge_token': challenge['challenge_token'], 'code': 'xxxxxx'}).status_code == 400
    assert db_session.get(UserMFA, auth_user.id).failed_attempts == 10
    assert login(client, auth_user).status_code == 429
    clock[0] += 901
    assert login(client, auth_user).json()['mfa_required']


def test_operator_step_up_and_opt_in_policy(client, auth_user, auth_headers, db_session, clock, monkeypatch):
    auth_user.is_admin = True
    db_session.commit()
    assert client.get('/api/admin/organizations', headers=auth_headers).status_code == 200
    monkeypatch.setattr(mfa_settings, 'MFA_REQUIRED_FOR_OPERATORS', True)
    result = client.get('/api/admin/organizations', headers=auth_headers)
    assert result.status_code == 403 and result.json()['detail']['code'] == 'mfa_enrollment_required'
    assert client.get('/api/auth/mfa/status', headers=auth_headers).status_code == 200
    setup, confirmed, _ = enroll(client, auth_user, clock)
    assert client.get('/api/admin/organizations', headers=headers(confirmed)).status_code == 200
    clock[0] += mfa_settings.MFA_STEP_UP_SECONDS + 1
    result = client.get('/api/admin/organizations', headers=headers(confirmed))
    assert result.status_code == 403 and result.json()['detail']['code'] == 'mfa_step_up_required'
    fresh = client.post('/api/auth/mfa/step-up', headers=headers(confirmed), json={'code': svc.otp(setup['secret'], clock[0]//30)})
    assert fresh.status_code == 200, fresh.text
    assert client.get('/api/admin/organizations', headers=headers(fresh.json())).status_code == 200
    assert client.post('/api/auth/mfa/disable', headers=headers(fresh.json()), json={'password': 'password123', 'code': confirmed['recovery_codes'][0]}).status_code == 403


def test_recovery_rotation_and_disable_require_both_factors(client, auth_user, db_session, clock):
    _, confirmed, _ = enroll(client, auth_user, clock)
    payload = {'password': 'wrong', 'code': confirmed['recovery_codes'][0]}
    assert client.post('/api/auth/mfa/recovery-codes', headers=headers(confirmed), json=payload).status_code == 400
    payload['password'] = 'password123'
    result = client.post('/api/auth/mfa/recovery-codes', headers=headers(confirmed), json=payload)
    assert result.status_code == 200, result.text
    replaced = result.json()
    assert len(replaced['recovery_codes']) == 10
    assert not set(replaced['recovery_codes']) & set(confirmed['recovery_codes'])
    payload['code'] = replaced['recovery_codes'][0]
    result = client.post('/api/auth/mfa/disable', headers=headers(replaced), json=payload)
    assert result.status_code == 200
    assert not db_session.get(UserMFA, auth_user.id).secret_encrypted
    assert not db_session.query(MFARecoveryCode).filter_by(user_id=auth_user.id).count()
    assert client.get('/api/auth/me', headers=headers(confirmed)).status_code == 401
    assert 'access_token' in login(client, auth_user).json()


def test_password_change_preserves_mfa_without_refreshing_step_up(client, auth_user, clock):
    _, confirmed, _ = enroll(client, auth_user, clock)
    original_claims = decode_access_token(confirmed['access_token'])
    clock[0] += 601
    result = client.post('/api/change-password', headers=headers(confirmed), json={'old_password': 'password123', 'new_password': 'new-password1234'})
    assert result.status_code == 200, result.text
    claims = decode_access_token(result.json()['access_token'])
    assert claims['amr'] == original_claims['amr'] and claims['mfa_at'] == original_claims['mfa_at']
    assert client.get('/api/auth/me', headers=headers(result.json())).status_code == 200
    assert login(client, auth_user, 'new-password1234').json()['mfa_required']


@pytest.mark.parametrize('kind', ['oidc', 'lti'])
def test_federation_exchange_cannot_skip_mfa_or_ignore_revocation(client, auth_user, db_session, clock, kind):
    from app.models.administration import Organization
    from app.models.integrations import OIDCProvider, OIDCSession, LTIPlatform, LTISession
    from app.services.integration_secrets import seal
    org = Organization(name='MFA federation')
    db_session.add(org); db_session.flush()
    auth_user.organization_id = org.id
    db_session.commit()
    setup, _, _ = enroll(client, auth_user, clock)
    common = dict(organization_id=org.id, name='IdP', issuer='https://issuer.example',
        client_id='test', authorization_endpoint='https://issuer.example/authorize',
        token_endpoint='https://issuer.example/token', jwks_uri='https://issuer.example/jwks')
    provider = OIDCProvider(**common, client_secret_encrypted=seal('test')) if kind == 'oidc' else LTIPlatform(
        **common, created_by=auth_user.id, deployment_id='test', private_key_encrypted=seal('test'), public_jwk={})
    db_session.add(provider); db_session.flush()
    state, code, browser = [secrets.token_urlsafe(32) for _ in range(3)]
    common = dict(state_hash=svc.digest(state), nonce_hash=svc.digest('nonce'), browser_hash=svc.digest(browser),
        expires_at=svc.now()+timedelta(minutes=5), exchange_code_hash=svc.digest(code), authenticated_user_id=auth_user.id)
    row = OIDCSession(**common, provider_id=provider.id, verifier_encrypted=seal('verifier')) if kind == 'oidc' else LTISession(**common, platform_id=provider.id)
    db_session.add(row); db_session.commit()
    prefix = '/api/auth/oidc' if kind == 'oidc' else '/api/lti'
    client.cookies.set('lernium_'+kind+'_'+state[:12], browser, path=prefix)
    result = client.post(prefix+'/exchange', json={'code': code, 'state': state})
    assert result.status_code == 200, result.text
    challenge = result.json()
    assert challenge['mfa_required'] and 'access_token' not in challenge
    provider.enabled = False; db_session.commit()
    clock[0] += 31
    result = client.post('/api/auth/mfa/verify', json={'challenge_token': challenge['challenge_token'], 'code': svc.otp(setup['secret'], clock[0]//30)})
    assert result.status_code == 403, result.text


def test_expired_and_auth_version_revoked_challenges_cannot_issue_session(client, auth_user, db_session, clock):
    _, confirmed, _ = enroll(client, auth_user, clock)
    challenge = login(client, auth_user).json()
    clock[0] += 301
    payload = {'challenge_token': challenge['challenge_token'], 'code': confirmed['recovery_codes'][0]}
    assert client.post('/api/auth/mfa/verify', json=payload).status_code == 400
    payload['challenge_token'] = login(client, auth_user).json()['challenge_token']
    auth_user.auth_version += 1; db_session.commit()
    assert client.post('/api/auth/mfa/verify', json=payload).status_code == 400

