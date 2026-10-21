import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import SecretStr
from app.core.config import settings
from app.core.integration_config import integration_settings
from app.core.security import create_access_token, hash_password
from app.models.administration import Organization
from app.models.integrations import OutboundMessage, IntegrationKey, WebhookEndpoint, BillingAccount, Invoice, PaymentEvent, OIDCProvider, OIDCSession, ExternalIdentity, LTIPlatform, LTIGradeBinding, LTIResource, LTIIdentity
from app.models.user import User
import app.models.learning
from app.services import mail_service, outbox_service, integration_http, billing_service, oidc_service, lti_service
from app.services.integration_secrets import seal, unseal


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setattr(integration_settings,"DATA_ENCRYPTION_KEY",SecretStr(Fernet.generate_key().decode()))


@pytest.fixture
def owner(db_session,auth_user):
    org=Organization(name="Integration tenant")
    db_session.add(org);db_session.flush()
    auth_user.organization_id=org.id;auth_user.organization_role="owner"
    db_session.commit();return auth_user


def key_payload(scopes=None):
    return {"name":"Customer connector","scopes":scopes or ["members:read"],"expires_at":(datetime.utcnow()+timedelta(days=30)).isoformat()}


def test_keys_are_hashed_scoped_and_rotation_revokes_previous(client,db_session,owner,auth_headers):
    result=client.post('/api/integrations/keys',headers=auth_headers,json=key_payload())
    assert result.status_code==201,result.text
    row=result.json();raw=row['key'];headers={'Authorization':'Bearer '+raw}
    assert raw not in client.get('/api/integrations/keys',headers=auth_headers).text
    assert db_session.get(IntegrationKey,row['id']).key_hash==hashlib.sha256(raw.encode()).hexdigest()
    members=client.get('/api/v1/members',headers=headers)
    assert members.status_code==200 and members.json()['total']==1
    assert client.get('/api/v1/courses',headers=headers).status_code==403
    rotated=client.post(f"/api/integrations/keys/{row['id']}/rotate",headers=auth_headers)
    assert rotated.status_code==201
    assert client.get('/api/v1/members',headers=headers).status_code==401
    assert client.get('/api/v1/members',headers={'Authorization':'Bearer '+rotated.json()['key']}).status_code==200


def test_integration_keys_never_impersonate_users_or_cross_tenants(client,db_session,owner,auth_headers):
    other=Organization(name="Other tenant");db_session.add(other);db_session.flush()
    db_session.add(User(email="outsider@example.org",password_hash=hash_password("secret-passphrase"),organization_id=other.id))
    db_session.commit()
    key=client.post('/api/integrations/keys',headers=auth_headers,json=key_payload()).json()['key']
    headers={'Authorization':'Bearer '+key}
    assert client.get('/api/v1/members',headers=headers).json()['total']==1
    assert client.get('/api/auth/me',headers=headers).status_code==401
    owner.organization_role="member";db_session.commit()
    assert client.get('/api/integrations/keys',headers=auth_headers).status_code==403


def test_expired_revoked_and_suspended_keys_fail_closed(client,db_session,owner,auth_headers):
    result=client.post('/api/integrations/keys',headers=auth_headers,json=key_payload()).json()
    key=db_session.get(IntegrationKey,result['id']);key.expires_at=datetime.utcnow()-timedelta(seconds=1);db_session.commit()
    assert client.get('/api/v1/members',headers={'Authorization':'Bearer '+result['key']}).status_code==401
    key.expires_at=datetime.utcnow()+timedelta(days=1);owner.organization.is_active=False;db_session.commit()
    assert client.get('/api/v1/members',headers={'Authorization':'Bearer '+result['key']}).status_code==403


def test_disabled_delivery_option_blocks_keys_and_federation_but_preserves_history_and_billing(client,db_session,owner,auth_headers):
    from app.models.learning import OrganizationLearningSettings
    result=client.post('/api/integrations/keys',headers=auth_headers,json=key_payload()).json()
    db_session.add(OrganizationLearningSettings(organization_id=owner.organization_id,features={"integrations":False}))
    db_session.commit()
    assert client.get('/api/v1/members',headers={'Authorization':'Bearer '+result['key']}).status_code==403
    assert client.post('/api/integrations/keys',headers=auth_headers,json=key_payload()).status_code==403
    assert client.get('/api/integrations/keys',headers=auth_headers).status_code==200
    assert client.get('/api/billing',headers=auth_headers).status_code==200
    assert client.delete('/api/integrations/keys/'+str(result['id']),headers=auth_headers).status_code==200
    from types import SimpleNamespace
    provider=SimpleNamespace(organization_id=owner.organization_id)
    for action in (lambda:oidc_service.begin(db_session,provider),lambda:lti_service.begin(db_session,provider,'learner')):
        with pytest.raises(HTTPException) as exc:action()
        assert exc.value.status_code==403
    outbox_service.enqueue_event(db_session,owner.organization_id,'course.published','disabled-event',{})
    assert not db_session.query(OutboundMessage).count()


def test_mail_queue_survives_failure_retries_and_erases_token_after_delivery(db_session,monkeypatch):
    monkeypatch.setattr(mail_service,'configured',lambda:True)
    row=mail_service.queue_reset(db_session,'learner@example.org','https://example.org/reset#token=private-token','hash','en')
    db_session.commit();assert 'private-token' not in row.payload_encrypted
    assert 'reset your password' in unseal(row.payload_encrypted)['subject']
    def unavailable(*args):raise RuntimeError('SMTP password must not enter the ledger')
    monkeypatch.setattr(mail_service,'send_account_mail',unavailable)
    assert outbox_service.process_one(db_session)
    db_session.refresh(row);assert row.status=='pending' and row.attempts==1 and row.last_error=='RuntimeError'
    assert row.payload_encrypted and row.available_at>datetime.utcnow()
    delivered=[];monkeypatch.setattr(mail_service,'send_account_mail',lambda *args:delivered.append(args))
    row.available_at=datetime.utcnow()-timedelta(seconds=1);db_session.commit()
    outbox_service.process_one(db_session);db_session.refresh(row)
    assert len(delivered)==1 and delivered[0][-1]==row.id
    assert row.status=='delivered' and row.payload_encrypted==''
    assert not outbox_service.process_one(db_session)


def test_expired_messages_are_not_delivered_and_abandoned_leases_resume(db_session,monkeypatch):
    monkeypatch.setattr(mail_service,'configured',lambda:True)
    row=mail_service.queue_mail(db_session,'x@example.org','Hello','private',expires_at=datetime.utcnow()-timedelta(seconds=1))
    db_session.commit();called=[]
    monkeypatch.setattr(mail_service,'send_account_mail',lambda *args:called.append(args))
    outbox_service.process_one(db_session);db_session.refresh(row)
    assert row.status=='expired' and not called and not row.payload_encrypted
    retry=mail_service.queue_mail(db_session,'x@example.org','Hello','private');db_session.commit()
    claimed=outbox_service.claim(db_session);assert claimed.id==retry.id
    assert outbox_service.claim(db_session) is None
    retry.lease_until=datetime.utcnow()-timedelta(seconds=1);db_session.commit()
    outbox_service.process_one(db_session);db_session.refresh(retry)
    assert retry.status=='delivered' and retry.attempts==2


def test_webhook_signing_and_duplicate_domain_events(db_session,owner,monkeypatch):
    secret='only-receiver-knows';endpoint=WebhookEndpoint(organization_id=owner.organization_id,url='https://customer.example/webhook',secret_encrypted=seal(secret),events=['course.published'])
    db_session.add(endpoint);db_session.flush()
    outbox_service.enqueue_event(db_session,owner.organization_id,'course.published','course:42:v1',{'course_id':42})
    outbox_service.enqueue_event(db_session,owner.organization_id,'course.published','course:42:v1',{'course_id':42})
    db_session.commit();assert db_session.query(OutboundMessage).count()==1
    requests=[];monkeypatch.setattr(integration_http,'request',lambda *args:requests.append(args))
    outbox_service.process_one(db_session)
    import hmac
    _,_,body,headers=requests[0];parts=dict(part.split('=') for part in headers['X-Lernium-Signature'].split(','))
    expected=hmac.new(secret.encode(),parts['t'].encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert parts['v1']==expected and headers['X-Lernium-Event-ID']=='course:42:v1'


def test_ssrf_destinations_and_mixed_dns_are_rejected(monkeypatch):
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):integration_http.validate_endpoint('https://localhost/private')
    with pytest.raises(ValueError):integration_http.validate_endpoint('http://example.org')
    with pytest.raises(ValueError):integration_http.validate_endpoint('https://user:password@example.org')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('10.1.1.1',443))])
    with pytest.raises(ValueError):integration_http.validate_endpoint('https://rebind.example.org')


def billing_fixture(db_session,owner):
    account=BillingAccount(organization_id=owner.organization_id,plan_name='Negotiated plan',contract_reference='Contract 1',currency='RUB',terms={'agreed':True})
    invoice=Invoice(id=str(uuid.uuid4()),organization_id=owner.organization_id,number='INV-1',amount_minor=10000,currency='RUB',description='Contracted service',due_at=datetime.utcnow()+timedelta(days=7),service_until=datetime.utcnow()+timedelta(days=30))
    db_session.add_all([account,invoice]);db_session.commit();return account,invoice


def test_only_admin_can_confirm_invoice_and_duplicate_is_safe(client,db_session,owner,auth_headers):
    account,invoice=billing_fixture(db_session,owner)
    payload={'payment_reference':'BANK-REF-001','reason':'Verified bank statement dated 21 October'}
    assert client.post(f'/api/admin/invoices/{invoice.id}/confirm',headers=auth_headers,json=payload).status_code==403
    assert db_session.get(BillingAccount,owner.organization_id).status=='pending'
    owner.is_admin=True;db_session.commit()
    assert client.post(f'/api/admin/invoices/{invoice.id}/confirm',headers=auth_headers,json=payload).status_code==200
    assert client.post(f'/api/admin/invoices/{invoice.id}/confirm',headers=auth_headers,json=payload).status_code==200
    assert db_session.query(PaymentEvent).count()==1
    db_session.refresh(account);assert account.status=='active' and account.access_until==invoice.service_until
    changed=payload|{'payment_reference':'DIFFERENT'}
    assert client.post(f'/api/admin/invoices/{invoice.id}/confirm',headers=auth_headers,json=changed).status_code==409


def test_provider_notifications_verify_server_object_not_forged_body(client,db_session,owner,monkeypatch):
    account,invoice=billing_fixture(db_session,owner);payment_id=str(uuid.uuid4())
    forged={'type':'notification','event':'payment.succeeded','object':{'id':payment_id,'paid':True,'amount':{'value':'100.00','currency':'RUB'}}}
    verified={'id':payment_id,'status':'succeeded','paid':True,'test':False,'amount':{'value':'99.00','currency':'RUB'},'metadata':{'invoice_id':invoice.id,'organization_id':str(owner.organization_id)}}
    monkeypatch.setattr(billing_service,'fetch_verified_payment',lambda _:verified)
    assert client.post('/api/billing/webhooks/yookassa',json=forged).status_code==409
    db_session.refresh(invoice);assert invoice.status=='issued'
    verified['amount']['value']='100.00'
    assert client.post('/api/billing/webhooks/yookassa',json=forged).status_code==200
    assert client.post('/api/billing/webhooks/yookassa',json=forged).status_code==200
    assert db_session.query(PaymentEvent).count()==1


def oidc_fixture(db_session,owner):
    provider=OIDCProvider(organization_id=owner.organization_id,name='Customer SSO',issuer='https://sso.example.org',client_id='lernium',client_secret_encrypted=seal('client-secret'),authorization_endpoint='https://sso.example.org/authorize',token_endpoint='https://sso.example.org/token',jwks_uri='https://sso.example.org/jwks',allow_provisioning=True,allowed_domains=['example.org'])
    db_session.add(provider);db_session.commit();return provider


def test_oidc_signature_nonce_issuer_and_multi_audience_are_validated(db_session,owner,monkeypatch):
    provider=oidc_fixture(db_session,owner);private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))|{'kid':'key-1','alg':'RS256'}
    monkeypatch.setattr(integration_http,'request_json',lambda *a,**k:{'keys':[public]})
    now=datetime.now(timezone.utc)
    claims={'iss':provider.issuer,'sub':'person-1','aud':provider.client_id,'iat':now,'exp':now+timedelta(minutes=5),'nonce':'unique-nonce'}
    sign=lambda body:jwt.encode(body,private,algorithm='RS256',headers={'kid':'key-1'})
    assert oidc_service.verify_id_token(sign(claims),provider,oidc_service.digest('unique-nonce'))['sub']=='person-1'
    for changed in (claims|{'nonce':'wrong'},claims|{'iss':'https://attacker.example'},claims|{'aud':['lernium','another'],'azp':'another'}):
        with pytest.raises(HTTPException):oidc_service.verify_id_token(sign(changed),provider,oidc_service.digest('unique-nonce'))


def test_oidc_does_not_capture_existing_email_and_link_is_explicit(db_session,owner):
    provider=oidc_fixture(db_session,owner)
    claims={'sub':'existing-at-provider','email':owner.email,'email_verified':True}
    provider.allowed_domains=[owner.email.rsplit('@',1)[1]]
    with pytest.raises(HTTPException) as error:oidc_service.resolve_identity(db_session,provider,claims)
    assert error.value.status_code==409
    user=oidc_service.resolve_identity(db_session,provider,claims,link_user_id=owner.id)
    assert user.id==owner.id
    db_session.commit();assert db_session.query(ExternalIdentity).count()==1


def test_oidc_browser_state_and_single_use_exchange(client,db_session,owner,monkeypatch):
    provider=oidc_fixture(db_session,owner)
    _,state,browser=oidc_service.begin(db_session,provider)
    with pytest.raises(HTTPException):oidc_service.complete(db_session,state,'authorization-code','wrong-browser')
    row=db_session.get(OIDCSession,oidc_service.digest(state));assert row.consumed_at is None
    monkeypatch.setattr(integration_http,'request_json',lambda *a,**k:{'id_token':'verified-by-test'})
    monkeypatch.setattr(oidc_service,'verify_id_token',lambda *a,**k:{'sub':'learner-subject','email':'new@example.org','email_verified':True,'name':'Learner'})
    exchange=oidc_service.complete(db_session,state,'authorization-code',browser)
    with pytest.raises(HTTPException):oidc_service.complete(db_session,state,'authorization-code',browser)
    client.cookies.set('lernium_oidc_'+state[:12],browser,path='/api/auth/oidc')
    response=client.post('/api/auth/oidc/exchange',json={'code':exchange,'state':state})
    assert response.status_code==200,response.text
    assert client.post('/api/auth/oidc/exchange',json={'code':exchange,'state':state}).status_code==400


def published_fixture(db,owner):
    from app.models.course import Course
    from app.models.course_version import CourseVersion
    from app.models.learning import PublishedLearningContent
    course=Course(name='Published training',owner_id=owner.id,organization_id=owner.organization_id,status='ready',publication_status='published')
    db.add(course);db.flush()
    version=CourseVersion(course_id=course.id,revision=1,name=course.name,publication_status='published')
    db.add(version);db.flush()
    db.add(PublishedLearningContent(course_version_id=version.id,organization_id=owner.organization_id,
        snapshot={'course':{'id':course.id,'title':course.name},'modules':[{'id':1,'title':'Module','lessons':[{'id':1,'title':'Lesson','content':'Immutable lesson'}],'assessments':[]}],'assessments':[]}))
    db.commit();return version


def test_assignment_api_idempotency_conflict_and_tenant_boundary(client,db_session,owner,auth_headers):
    version=published_fixture(db_session,owner)
    raw=client.post('/api/integrations/keys',headers=auth_headers,json=key_payload(['assignments:write','results:read'])).json()['key']
    headers={'Authorization':'Bearer '+raw}
    payload={'course_version_id':version.id,'user_id':owner.id,'external_id':'hr:2026:owner'}
    first=client.post('/api/v1/assignments',headers=headers,json=payload)
    assert first.status_code==200,first.text
    second=client.post('/api/v1/assignments',headers=headers,json=payload)
    assert second.status_code==200 and second.json()['created'] is False
    assert second.json()['assignment']['id']==first.json()['assignment']['id']
    assert client.post('/api/v1/assignments',headers=headers,json=payload|{'user_id':9999}).status_code==409
    other=Organization(name='Foreign tenant');db_session.add(other);db_session.flush()
    outsider=User(email='foreign@example.org',password_hash='unusable',organization_id=other.id);db_session.add(outsider);db_session.commit()
    assert client.post('/api/v1/assignments',headers=headers,json=payload|{'external_id':'different','user_id':outsider.id}).status_code==404
    result=client.get('/api/v1/results',headers=headers).json();assert result['total']==1
    assert 'correct_answers' not in json.dumps(result)
    assignment_id=first.json()['assignment']['id']
    assert client.delete(f'/api/v1/assignments/{assignment_id}',headers=headers).status_code==200
    assert client.get('/api/v1/results',headers=headers).json()['items'][0]['state']=='revoked'


def test_expired_contract_preserves_reads_and_history_but_blocks_new_work(client,db_session,owner,auth_headers):
    account,invoice=billing_fixture(db_session,owner)
    account.status='active';account.access_until=datetime.utcnow()-timedelta(days=1);db_session.commit()
    assert client.get('/api/billing',headers=auth_headers).status_code==200
    assert client.patch('/api/me',headers=auth_headers,json={'full_name':'New Name'}).status_code==200
    assert client.post('/api/courses/drafts',headers=auth_headers,json={}).status_code==403
    assert db_session.get(Invoice,invoice.id).status=='issued'


def test_lti_signed_launch_replay_deployment_and_latest_grade_delivery(client,db_session,owner,monkeypatch):
    from cryptography.hazmat.primitives import serialization
    version=published_fixture(db_session,owner)
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))|{'kid':'lti-key','alg':'RS256'}
    pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    platform=LTIPlatform(organization_id=owner.organization_id,created_by=owner.id,name='Test LMS',issuer='https://lms.example.org',client_id='lernium-client',deployment_id='deployment-one',authorization_endpoint='https://lms.example.org/authorize',token_endpoint='https://lms.example.org/token',jwks_uri='https://lms.example.org/jwks',private_key_encrypted=seal(pem),public_jwk=public)
    db_session.add(platform);db_session.flush()
    db_session.add_all([LTIResource(platform_id=platform.id,resource_link_id='course-link',course_version_id=version.id),LTIIdentity(platform_id=platform.id,subject='lms-user-1',user_id=owner.id)])
    db_session.commit()
    url,state,browser=lti_service.begin(db_session,platform,'login-hint')
    nonce=parse_qs(urlsplit(url).query)['nonce'][0]
    now=datetime.now(timezone.utc)
    claims={'iss':platform.issuer,'sub':'lms-user-1','aud':platform.client_id,'iat':now,'exp':now+timedelta(minutes=5),'nonce':nonce,
        lti_service.CLAIM+'version':'1.3.0',lti_service.CLAIM+'message_type':'LtiResourceLinkRequest',lti_service.CLAIM+'deployment_id':'deployment-one',
        lti_service.CLAIM+'roles':['http://purl.imsglobal.org/vocab/lis/v2/membership#Learner'],
        lti_service.CLAIM+'target_link_uri':lti_service.launch_url(platform),lti_service.CLAIM+'resource_link':{'id':'course-link'},
        lti_service.AGS_CLAIM:{'lineitem':'https://lms.example.org/lineitems/42','scope':[lti_service.SCORE_SCOPE]}}
    monkeypatch.setattr(integration_http,'request_json',lambda *a,**k:{'keys':[public]})
    monkeypatch.setattr(integration_http,'validate_endpoint',lambda url:None)
    sign=lambda data:jwt.encode(data,private,algorithm='RS256',headers={'kid':'lti-key'})
    with pytest.raises(HTTPException):lti_service.complete(db_session,platform,state,sign(claims|{lti_service.CLAIM+'deployment_id':'wrong'}),browser)
    code=lti_service.complete(db_session,platform,state,sign(claims),browser)
    with pytest.raises(HTTPException):lti_service.complete(db_session,platform,state,sign(claims),browser)
    client.cookies.set('lernium_lti_'+state[:12],browser,path='/api/lti')
    exchanged=client.post('/api/lti/exchange',json={'code':code,'state':state})
    assert exchanged.status_code==200,exchanged.text
    assignment_id=exchanged.json()['assignment_id']
    assert client.post('/api/lti/exchange',json={'code':code,'state':state}).status_code==400
    first=datetime.utcnow();latest=first+timedelta(seconds=1)
    lti_service.queue_grade(db_session,assignment_id,70,100,first);db_session.flush()
    lti_service.queue_grade(db_session,assignment_id,80,100,latest);db_session.flush()
    lti_service.queue_grade(db_session,assignment_id,60,100,first);db_session.commit()
    assert db_session.query(OutboundMessage).count()==2
    monkeypatch.setattr(integration_http,'request_json',lambda *a,**k:{'access_token':'lms-service-token','token_type':'Bearer'})
    sent=[];monkeypatch.setattr(integration_http,'request',lambda *args:sent.append(args))
    while outbox_service.process_one(db_session):pass
    assert len(sent)==1 and sent[0][1].endswith('/scores')
    assert json.loads(sent[0][2])['scoreGiven']==80
    assert db_session.query(LTIGradeBinding).one().last_delivered_at==latest
