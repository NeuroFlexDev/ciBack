import hashlib
from datetime import datetime, timedelta
from app.core.security import create_access_token, verify_password
from app.models.account import PasswordReset
from app.models.user import User
from app.services import mail_service
from app.models.integrations import OutboundMessage
from app.services.integration_secrets import unseal
from app.core.integration_config import integration_settings
from cryptography.fernet import Fernet
from pydantic import SecretStr


def test_profile_and_password_change_revoke_previous_access(client, auth_headers, auth_user):
    assert client.patch('/api/me', headers=auth_headers, json={'full_name':'Анна Петрова'}).json()['full_name'] == 'Анна Петрова'
    result = client.post('/api/change-password', headers=auth_headers, json={'old_password':'password123','new_password':'new-long-password-2026'})
    assert result.status_code == 200
    assert client.get('/api/auth/me', headers=auth_headers).status_code == 401
    new_headers = {'Authorization':'Bearer '+result.json()['access_token']}
    assert client.get('/api/auth/me', headers=new_headers).status_code == 200
    assert client.post('/api/auth/logout-all', headers=new_headers).status_code == 200
    assert client.get('/api/auth/me', headers=new_headers).status_code == 401


def test_legacy_passwords_remain_usable_and_are_upgraded(client, db_session):
    salt='legacy-salt'
    old=salt+'$'+hashlib.pbkdf2_hmac('sha256',b'old-password-123',salt.encode(),120000).hex()
    user=User(email='legacy@example.edu',password_hash=old)
    db_session.add(user);db_session.commit()
    assert verify_password('old-password-123', old)
    assert client.post('/api/auth/login',json={'email':user.email,'password':'old-password-123'}).status_code == 200
    db_session.refresh(user)
    assert user.password_hash.startswith('pbkdf2_sha256$600000$')


def test_reset_is_single_use_and_revokes_existing_tokens(client, db_session, auth_user, auth_headers, monkeypatch):
    monkeypatch.setattr(mail_service,'configured',lambda:True)
    monkeypatch.setattr(integration_settings,'DATA_ENCRYPTION_KEY',SecretStr(Fernet.generate_key().decode()))
    known=client.post('/api/auth/password-reset',json={'email':auth_user.email})
    unknown=client.post('/api/auth/password-reset',json={'email':'absent@example.edu'})
    assert known.json()==unknown.json()
    delivered=db_session.query(OutboundMessage).all()
    assert len(delivered)==1 and delivered[0].status=='pending'
    raw=unseal(delivered[0].payload_encrypted)['text'].split('#token=')[1].splitlines()[0]
    assert db_session.get(PasswordReset,hashlib.sha256(raw.encode()).hexdigest()) is not None
    payload={'token':raw,'new_password':'a-new-long-password'}
    assert client.post('/api/auth/password-reset/confirm',json=payload).status_code == 200
    assert client.post('/api/auth/password-reset/confirm',json=payload).status_code == 400
    assert client.get('/api/auth/me',headers=auth_headers).status_code == 401
    assert client.post('/api/auth/login',json={'email':auth_user.email,'password':payload['new_password']}).status_code == 200


def test_expired_reset_and_disabled_account_are_rejected(client, db_session, auth_user, auth_headers):
    raw='x'*40
    db_session.add(PasswordReset(token_hash=hashlib.sha256(raw.encode()).hexdigest(),user_id=auth_user.id,expires_at=datetime.utcnow()-timedelta(seconds=1)))
    db_session.commit()
    assert client.post('/api/auth/password-reset/confirm',json={'token':raw,'new_password':'new-long-password'}).status_code == 400
    auth_user.is_active=False;db_session.commit()
    assert client.get('/api/auth/me',headers=auth_headers).status_code == 401
    assert client.post('/api/auth/login',json={'email':auth_user.email,'password':'password123'}).status_code == 401


def test_profile_cannot_assign_privileges_or_change_unverified_email(client, auth_headers):
    assert client.patch('/api/me',headers=auth_headers,json={'full_name':'Name','is_active':True}).status_code == 422
    assert client.patch('/api/me',headers=auth_headers,json={'full_name':'Name','email':'another@example.edu'}).status_code == 422
    assert client.patch('/api/me',headers=auth_headers,json={'full_name':'   '}).status_code == 422
