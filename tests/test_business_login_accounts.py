import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlsplit, parse_qs
import pytest
from app.models.user import User
from app.models.account import EmailBinding
from app.models.administration import AdminEvent
from app.models.learning import LearningPerson
from app.core.security import verify_password
from tests.test_business_positions import company, headers, make_position

PASSWORD = 'employee-password-2026'


def create(client, actor, **extra):
    return client.post('/api/business/employees', headers=headers(actor), json={
        'full_name':'Иван Петров', 'username':'beslim.ivan', 'password':PASSWORD, **extra})


def test_owner_creates_login_only_employee_and_can_reset_access(client, db_session, company):
    org, u = company
    position = make_position(client, u['manager'])
    response = create(client, u['owner'], username='  BeSlim.Ivan ', manager_id=u['manager'].id, position_id=position['id'])
    assert response.status_code == 201, response.text
    data=response.json(); employee=db_session.get(User,data['id'])
    assert data['email'] is None and data['username']=='beslim.ivan' and 'password' not in response.text
    assert employee.organization_id==org.id and employee.manager_id==u['manager'].id and not employee.is_admin
    assert verify_password(PASSWORD,employee.password_hash)
    assert db_session.get(LearningPerson,employee.id).position_id==position['id']
    login=client.post('/api/auth/login',json={'email':'BESLIM.IVAN','password':PASSWORD})
    assert login.status_code==200,login.text
    old={'Authorization':'Bearer '+login.json()['access_token']}
    assert client.get('/api/auth/me',headers=old).json()['email'] is None
    assert client.get('/api/business/employees?q=beslim.ivan',headers=headers(u['owner'])).json()['total']==1
    reset=client.post(f'/api/business/employees/{employee.id}/password',headers=headers(u['owner']),json={'new_password':PASSWORD+'2','expected_auth_version':0})
    assert reset.status_code==200
    assert client.get('/api/auth/me',headers=old).status_code==401
    assert client.post('/api/auth/login',json={'email':'beslim.ivan','password':PASSWORD}).status_code==401
    assert client.post('/api/auth/login',json={'email':'beslim.ivan','password':PASSWORD+'2'}).status_code==200
    assert PASSWORD not in str([row.details for row in db_session.query(AdminEvent)])


def test_company_creation_enforces_roles_scope_seats_and_unique_login(client, db_session, company):
    org,u=company
    assert create(client,u['employee']).status_code==403
    assert create(client,u['government']).status_code==403
    assert create(client,u['manager'],role='manager').status_code==403
    assert create(client,u['manager'],manager_id=u['outsider'].id).status_code==403
    foreign_position=make_position(client,u['outsider'])
    assert create(client,u['owner'],position_id=foreign_position['id']).status_code==404
    r=create(client,u['manager']);assert r.status_code==201,r.text
    assert r.json()['manager_id']==u['manager'].id
    assert create(client,u['outsider'],username='BESLIM.IVAN').status_code==409
    for bad in ['a@b.test','абв','two words','aa']:
        assert create(client,u['owner'],username=bad).status_code==422
    assert create(client,u['owner'],username='other',password='weak').status_code==422
    assert create(client,u['owner'],username='other',is_admin=True).status_code==422
    org.seat_limit=1;db_session.commit()
    assert create(client,u['owner'],username='one.more').status_code==409
    assert client.post('/api/auth/register',json={'email':'login.only','password':PASSWORD,'terms_accepted':True,'legal_version':'x'}).status_code==422


@pytest.fixture
def login_employee(client,db_session,company):
    _,u=company
    r=create(client,u['owner']);assert r.status_code==201,r.text
    return db_session.get(User,r.json()['id'])


@pytest.fixture
def mailbox(monkeypatch):
    from app.services import mail_service
    messages=[]
    monkeypatch.setattr(mail_service,'configured',lambda:True)
    monkeypatch.setattr(mail_service,'queue_mail',lambda db,recipient,subject,text,**kw:messages.append((recipient,text)))
    return messages


def request_binding(client,user,email='ivan@personal.test'):
    return client.post('/api/me/email',headers=headers(user),json={'email':email,'password':PASSWORD})


def mail_token(mailbox):
    url=mailbox[-1][1].splitlines()[-1]
    assert urlsplit(url).path=='/auth'
    return parse_qs(urlsplit(url).fragment)['email_token'][0]


def test_employee_binds_verified_email_keeps_login_and_rotates_session(client,db_session,login_employee,mailbox):
    user=login_employee;old=headers(user)
    response=request_binding(client,user)
    assert response.status_code==202,response.text
    token=mail_token(mailbox)
    assert user.email is None and token not in response.text
    row=db_session.query(EmailBinding).one()
    assert row.token_hash==hashlib.sha256(token.encode()).hexdigest()
    assert client.post('/api/auth/login',json={'email':'ivan@personal.test','password':PASSWORD}).status_code==401
    response=client.post('/api/me/email/confirm',headers=old,json={'token':token})
    assert response.status_code==200,response.text
    assert response.json()['user']['email']=='ivan@personal.test'
    assert client.get('/api/auth/me',headers=old).status_code==401
    new={'Authorization':'Bearer '+response.json()['access_token']}
    assert client.get('/api/auth/me',headers=new).status_code==200
    for identity in ['beslim.ivan','ivan@personal.test']:
        assert client.post('/api/auth/login',json={'email':identity,'password':PASSWORD}).status_code==200
    assert client.post('/api/me/email/confirm',headers=new,json={'token':token}).status_code==409
    assert row.consumed_at is not None


def test_binding_rejects_foreign_expired_superseded_and_password_changed_tokens(client,db_session,login_employee,company,mailbox):
    _,u=company;user=login_employee
    other=create(client,u['owner'],username='beslim.other');other_user=db_session.get(User,other.json()['id'])
    assert request_binding(client,user).status_code==202
    first=mail_token(mailbox)
    assert client.post('/api/me/email/confirm',headers=headers(other_user),json={'token':first}).status_code==400
    assert request_binding(client,user,'second@personal.test').status_code==202
    assert client.post('/api/me/email/confirm',headers=headers(user),json={'token':first}).status_code==400
    second=mail_token(mailbox)
    row=db_session.query(EmailBinding).filter_by(token_hash=hashlib.sha256(second.encode()).hexdigest()).one()
    row.expires_at=datetime.utcnow()-timedelta(seconds=1);db_session.commit()
    assert client.post('/api/me/email/confirm',headers=headers(user),json={'token':second}).status_code==400
    assert request_binding(client,user).status_code==202
    last=mail_token(mailbox);user.auth_version+=1;db_session.commit()
    assert client.post('/api/me/email/confirm',headers=headers(user),json={'token':last}).status_code==400
    assert user.email is None


def test_binding_needs_password_mail_transport_and_unclaimed_address(client,db_session,login_employee,company,mailbox,monkeypatch):
    from app.services import mail_service
    _,u=company;user=login_employee
    r=client.post('/api/me/email',headers=headers(user),json={'email':'ivan@personal.test','password':'wrong'})
    assert r.status_code==400 and mailbox==[]
    assert request_binding(client,user,u['owner'].email).status_code==409
    monkeypatch.setattr(mail_service,'configured',lambda:False)
    assert request_binding(client,user).status_code==503
    assert db_session.query(EmailBinding).count()==0 and user.email is None
