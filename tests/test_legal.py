from datetime import datetime, timedelta
from app.core.config import settings
from app.models.legal import LegalReceipt, LegalRequest
from app.models.user import User
from app.services.legal_service import CATALOG, DOCUMENTS


def registration(**changes):
    return {'email':'legal@example.edu','password':'long-enough-password','terms_accepted':True,
            'legal_version':CATALOG['version'], **changes}


def test_registration_requires_explicit_current_acceptance(client, db_session):
    for payload, code in [(registration(terms_accepted=False),422),
                          (registration(terms_accepted='true'),422),
                          (registration(legal_version='old'),409)]:
        assert client.post('/api/auth/register',json=payload).status_code == code
    assert not db_session.query(User).filter_by(email='legal@example.edu').first()
    assert db_session.query(LegalReceipt).count()==0


def test_registration_saves_text_atomically_without_implied_marketing(client,db_session):
    response=client.post('/api/auth/register',json=registration(full_name=' Анна '))
    assert response.status_code==200
    user=response.json()['user']
    assert user['full_name']=='Анна'
    rows=db_session.query(LegalReceipt).filter_by(user_id=user['id']).all()
    assert len(rows)==1
    assert rows[0].document_snapshot==DOCUMENTS['terms']
    assert rows[0].document_sha256==DOCUMENTS['terms']['sha256']
    assert rows[0].created_at is not None


def test_duplicate_registration_cannot_add_receipts(client,db_session):
    assert client.post('/api/auth/register',json=registration()).status_code==200
    count=db_session.query(LegalReceipt).count()
    assert client.post('/api/auth/register',json=registration(marketing_accepted=True)).status_code==409
    assert db_session.query(LegalReceipt).count()==count


def test_contacts_separate_consent_from_exercise_of_rights(client,db_session):
    payload={'purpose':'enquiry','email':'person@example.edu','message':'Прошу показать платформу',
             'legal_version':CATALOG['version'],'consent':False}
    assert client.post('/api/legal/requests',json=payload).status_code==422
    assert db_session.query(LegalRequest).count()==0
    accepted=client.post('/api/legal/requests',json={**payload,'consent':True})
    assert accepted.status_code==201
    assert db_session.get(LegalRequest,accepted.json()['reference']).status=='open'
    assert db_session.query(LegalReceipt).filter_by(document_slug='contact-consent').count()==1
    rights=client.post('/api/legal/requests',json={**payload,'purpose':'rights','legal_version':'old'})
    assert rights.status_code==201
    assert db_session.query(LegalReceipt).count()==1
    assert client.get('/api/legal/requests').status_code==405


def test_receipts_are_private_and_withdrawal_accepts_old_version(client,auth_headers,db_session):
    assert client.get('/api/legal/me/receipts').status_code==401
    registered=client.post('/api/auth/register',json=registration(marketing_accepted=True)).json()
    headers={'Authorization':'Bearer '+registered['access_token']}
    own=client.get('/api/legal/me/receipts',headers=headers).json()
    assert own['marketing_active'] is True
    assert len(own['receipts'])==2
    other=client.get('/api/legal/me/receipts',headers=auth_headers).json()
    assert other=={'marketing_active':False,'receipts':[]}
    assert client.put('/api/legal/me/marketing',headers=headers,json={'accepted':False,'legal_version':'old'}).status_code==200
    own=client.get('/api/legal/me/receipts',headers=headers).json()
    assert own['marketing_active'] is False
    assert [r['action'] for r in own['receipts']].count('accepted')==2
    assert own['receipts'][0]['action']=='withdrawn'


def test_marketing_expires_and_new_acceptance_needs_current_version(client,auth_headers,auth_user,db_session):
    from app.services.legal_service import record_receipt
    receipt=record_receipt(db_session,email=auth_user.email,user_id=auth_user.id,
                           slug='marketing-consent',context='account')
    receipt.created_at=datetime.utcnow()-timedelta(days=366)
    db_session.commit()
    assert client.get('/api/legal/me/receipts',headers=auth_headers).json()['marketing_active'] is False
    assert client.put('/api/legal/me/marketing',headers=auth_headers,json={'accepted':True,'legal_version':'old'}).status_code==409


def test_contact_collection_can_be_disabled(client,monkeypatch,db_session):
    monkeypatch.setattr(settings,'CONTACT_REQUESTS_ENABLED',False)
    response=client.post('/api/legal/requests',json={'purpose':'rights','email':'person@example.edu',
                        'message':'Прошу предоставить мои данные','legal_version':CATALOG['version']})
    assert response.status_code==503
    assert db_session.query(LegalRequest).count()==0
