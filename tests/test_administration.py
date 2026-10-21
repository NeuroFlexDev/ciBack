from datetime import datetime, timedelta
import hashlib
import pytest
from app.core.security import create_access_token, hash_password
from app.models.administration import Organization, Invitation, AdminEvent
from app.models.user import User
from app.models.course import Course
from app.models.legal import LegalReceipt
from app.services.legal_service import CATALOG
from app.services.organization_access import check_generation_limit
from fastapi import HTTPException


def headers(user):return {'Authorization':'Bearer '+create_access_token(user.id,user.auth_version)}


@pytest.fixture
def admin(db_session):
    u=User(email='owner@example.com',password_hash=hash_password('owner-password-123'),is_admin=True)
    db_session.add(u);db_session.commit();return u


def org(db,name='Organization',**kwargs):
    o=Organization(name=name,**kwargs);db.add(o);db.commit();return o


def member(db,o,email='member@example.com',role='member'):
    u=User(email=email,password_hash=hash_password('member-password-123'),organization_id=o.id,organization_role=role)
    db.add(u);db.commit();return u


def invite(client,admin,organization,email='new@example.com',role='member'):
    r=client.post('/api/admin/invitations',headers=headers(admin),json={'email':email,'organization_id':organization.id,'role':role})
    assert r.status_code==201,r.text
    return r.json(),r.json()['url'].split('#token=')[1]


def acceptance(token,**kwargs):
    return {'token':token,'password':'new-password-123456','full_name':'Test User','terms_accepted':True,'marketing_accepted':False,'legal_version':CATALOG['version'],**kwargs}


def test_admin_endpoints_require_database_role(client,auth_headers,auth_user):
    for path in ['overview','organizations','users','invitations','courses','requests','audit']:
        assert client.get('/api/admin/'+path,headers=auth_headers).status_code==403
        assert client.get('/api/admin/'+path).status_code==401
    assert client.post('/api/admin/organizations',headers=auth_headers,json={'name':'Denied'}).status_code==403
    assert client.patch('/api/me',headers=auth_headers,json={'full_name':'Unsafe','is_admin':True}).status_code==422
    assert not client.get('/api/auth/me',headers=auth_headers).json()['is_admin']


def test_invitation_acceptance_is_one_time_and_records_actual_choice(client,db_session,admin,monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings,'REGISTRATION_ENABLED',False)
    o=org(db_session,seat_limit=1)
    row,token=invite(client,admin,o)
    stored=db_session.get(Invitation,row['id'])
    assert stored.token_hash==hashlib.sha256(token.encode()).hexdigest()
    assert 'token' not in client.get('/api/admin/invitations',headers=headers(admin)).text
    assert client.post('/api/auth/invitations/accept',json=acceptance(token,terms_accepted=False)).status_code==422
    assert client.post('/api/auth/invitations/accept',json=acceptance(token,is_admin=True)).status_code==422
    r=client.post('/api/auth/invitations/accept',json=acceptance(token));assert r.status_code==201,r.text
    u=db_session.query(User).filter_by(email='new@example.com').one()
    assert not u.is_admin and u.organization_id==o.id
    assert [x.document_slug for x in db_session.query(LegalReceipt).filter_by(user_id=u.id)]==['terms']
    assert client.post('/api/auth/invitations/accept',json=acceptance(token)).status_code==410
    assert client.get('/api/auth/me',headers={'Authorization':'Bearer '+r.json()['access_token']}).status_code==200
    assert client.post('/api/admin/invitations',headers=headers(admin),json={'email':'extra@example.com','organization_id':o.id}).status_code==409


def test_revocation_expiry_and_suspended_organization(client,db_session,admin):
    o=org(db_session)
    row,token=invite(client,admin,o)
    assert client.delete('/api/admin/invitations/'+str(row['id']),headers=headers(admin)).status_code==200
    assert client.post('/api/auth/invitations/inspect',json={'token':token}).status_code==410
    row,token=invite(client,admin,o)
    db_session.get(Invitation,row['id']).expires_at=datetime.utcnow()-timedelta(seconds=1);db_session.commit()
    assert client.post('/api/auth/invitations/accept',json=acceptance(token)).status_code==410
    row,token=invite(client,admin,o)
    o.is_active=False;db_session.commit()
    assert client.post('/api/auth/invitations/accept',json=acceptance(token)).status_code==409


def test_invitation_cannot_take_over_existing_account(client,db_session,admin,auth_user):
    o=org(db_session)
    r=client.post('/api/admin/invitations',headers=headers(admin),json={'email':auth_user.email,'organization_id':o.id})
    assert r.status_code==201
    token=r.json()['url'].split('#token=')[1]
    # Receiving an invitation never mutates an existing account or its password.
    assert client.post('/api/auth/invitations/accept',json=acceptance(token)).status_code==409
    assert auth_user.organization_id is None


def test_team_manager_isolation_and_last_owner(client,db_session,admin):
    a,b=org(db_session,'Alpha'),org(db_session,'Beta')
    owner=member(db_session,a,'manager@example.com','owner');other=member(db_session,b,'other@example.com','owner')
    ordinary=member(db_session,a)
    r=client.get('/api/team',headers=headers(owner));assert len(r.json()['members'])==2
    assert all(x['organization_id']==a.id for x in r.json()['members'])
    payload={'email':'guest@example.com','organization_id':b.id}
    assert client.post('/api/team/invitations',headers=headers(owner),json=payload).status_code==403
    payload['organization_id']=a.id
    assert client.post('/api/team/invitations',headers=headers(ordinary),json=payload).status_code==403
    assert client.put('/api/team/members/'+str(other.id),headers=headers(owner),json={'is_active':False,'organization_id':a.id}).status_code==404
    assert client.put('/api/admin/users/'+str(owner.id),headers=headers(admin),json={'is_active':False,'organization_id':a.id}).status_code==409
    assert client.put('/api/admin/users/'+str(admin.id),headers=headers(admin),json={'is_active':False}).status_code==409


def test_disable_user_invalidates_existing_session(client,db_session,admin):
    o=org(db_session);u=member(db_session,o);h=headers(u)
    r=client.put('/api/admin/users/'+str(u.id),headers=headers(admin),json={'is_active':False,'organization_id':o.id})
    assert r.status_code==200,r.text
    assert client.get('/api/auth/me',headers=h).status_code==401
    assert client.post('/api/auth/login',json={'email':u.email,'password':'member-password-123'}).status_code==401


def test_suspend_org_revokes_member_access_without_deleting_data(client,db_session,admin):
    o=org(db_session);u=member(db_session,o)
    c=Course(name='Kept',owner_id=u.id);db_session.add(c);db_session.commit()
    assert client.put('/api/admin/organizations/'+str(o.id),headers=headers(admin),json={'name':'Paused','is_active':False}).status_code==200
    assert client.get('/api/courses/',headers=headers(u)).status_code==403
    assert db_session.get(Course,c.id).name=='Kept'
    assert client.get('/api/admin/overview',headers=headers(admin)).status_code==200


def test_real_course_quota_and_generation_zero(client,db_session,admin):
    o=org(db_session,course_limit=1,generation_limit=0);u=member(db_session,o,role='author')
    assert client.post('/api/courses/drafts',headers=headers(u)).status_code==201
    assert client.post('/api/courses/drafts',headers=headers(u)).status_code==409
    assert client.post('/api/courses/',headers=headers(u),json={'title':'Bypass','description':'No','level':1,'language':1}).status_code==409
    with pytest.raises(HTTPException) as e:check_generation_limit(db_session,u.id)
    assert e.value.status_code==409


def test_summary_scopes_personal_data_and_admin_can_assign_only_orphans(client,db_session,admin,auth_user,auth_headers):
    private=Course(name='Private',owner_id=admin.id);orphan=Course(name='Archive',owner_id=None)
    db_session.add_all([private,orphan]);db_session.commit()
    assert client.get('/api/workspace/overview',headers=auth_headers).json()['metrics']['courses']==0
    r=client.post(f'/api/admin/courses/{orphan.id}/assign-owner',headers=headers(admin),json={'user_id':auth_user.id})
    assert r.status_code==200,r.text
    assert client.get('/api/workspace/overview',headers=auth_headers).json()['metrics']['courses']==1
    assert client.post(f'/api/admin/courses/{private.id}/assign-owner',headers=headers(admin),json={'user_id':auth_user.id}).status_code==409
    assert db_session.query(AdminEvent).filter_by(action='course.owner_assigned').count()==1


def test_recovery_link_does_not_change_password_before_confirmation(client,db_session,admin,auth_user):
    original=auth_user.password_hash
    r=client.post(f'/api/admin/users/{auth_user.id}/recovery-link',headers=headers(admin));assert r.status_code==200
    assert auth_user.password_hash==original
    token=r.json()['url'].split('#token=')[1]
    assert client.post('/api/auth/password-reset/confirm',json={'token':token,'new_password':'changed-password-123'}).status_code==200
    assert client.post('/api/auth/password-reset/confirm',json={'token':token,'new_password':'another-password-123'}).status_code==400
    assert token not in client.get('/api/admin/audit',headers=headers(admin)).text


def test_source_quota_allows_replacement_and_frees_deleted_source(client, db_session, tmp_path):
    from app.models.document import Document
    from app.services.file_storage import LocalFileStorage, get_file_storage
    from main import app
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(tmp_path, 1024 * 1024)
    o = org(db_session, source_limit=1)
    u = member(db_session, o, role='author')
    course_id = client.post('/api/courses/drafts', headers=headers(u)).json()['id']
    path = f'/api/courses/{course_id}/documents'
    source = {'file': ('source.txt', b'Source material for quota check', 'text/plain')}
    first = client.post(path, headers=headers(u), files=source)
    assert first.status_code == 202, first.text
    assert client.post(path, headers=headers(u), files=source).status_code == 409
    replaced = client.post(path, headers=headers(u), files=source, data={'replace_document_id':first.json()['id']})
    assert replaced.status_code == 202, replaced.text
    # Historical versions must not continue to consume the source's slot.
    db_session.get(Document, first.json()['id']).is_current = False
    replacement = db_session.get(Document, replaced.json()['id'])
    replacement.is_current = True
    replacement.is_deleted = True
    db_session.commit()
    assert client.post(path, headers=headers(u), files=source).status_code == 202


def test_admin_bootstrap_preserves_password_and_revokes_old_tokens(client, db_session, auth_user, auth_headers):
    from scripts.platform_admin import grant
    password_hash = auth_user.password_hash
    assert grant(db_session, auth_user.email) == auth_user.id
    assert auth_user.is_admin and auth_user.password_hash == password_hash
    assert client.get('/api/auth/me', headers=auth_headers).status_code == 401
    assert client.get('/api/admin/overview', headers=headers(auth_user)).status_code == 200
    assert db_session.query(AdminEvent).filter_by(action='administrator.granted').count() == 1
    grant(db_session, auth_user.email)
    assert db_session.query(AdminEvent).filter_by(action='administrator.granted').count() == 1
    with pytest.raises(ValueError):grant(db_session, auth_user.email, 'never-overwrite-password')
    with pytest.raises(ValueError):grant(db_session, 'unknown@example.com')



def test_synchronous_graph_endpoint_cannot_bypass_monthly_limit(client, db_session):
    from tests.test_upload_pipeline import _course_and_document
    from app.models.generation_run import GenerationRun
    o = org(db_session, generation_limit=0)
    u = member(db_session, o, role='author')
    course, document, _ = _course_and_document(db_session, u)
    document.status = 'indexed'
    db_session.commit()
    result = client.post(f'/api/courses/{course.id}/generate-graph?force=true', headers=headers(u))
    assert result.status_code == 409, result.text
    assert 'месячный лимит' in result.json()['detail']
    assert db_session.query(GenerationRun).filter_by(course_id=course.id).count() == 0
