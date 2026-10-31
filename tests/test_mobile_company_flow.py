from app.core.security import hash_password
from app.models.integrations import MobileSession
from tests.test_business_positions import company
from tests.test_business_hierarchy import hierarchy


def test_four_mobile_roles_learning_notifications_and_team_scope(client, db_session, hierarchy):
    _, users, _, assignments = hierarchy
    auth = {}
    for role in ['owner', 'manager', 'employee', 'candidate']:
        users[role].password_hash = hash_password('mobile-acceptance-password')
    db_session.commit()
    for role in ['owner', 'manager', 'employee', 'candidate']:
        login = client.post('/api/auth/mobile/login', json={'email':users[role].email, 'password':'mobile-acceptance-password','device_name':'Acceptance Android'})
        assert login.status_code == 200, login.text
        auth[role] = {'Authorization':'Bearer '+login.json()['access_token']}
        assert login.json()['user']['organization_role'] == users[role].organization_role
    assert all(row.device_name == 'Acceptance Android' for row in db_session.query(MobileSession))
    assert client.get('/api/business/dashboard', headers=auth['owner']).json()['scope'] == 'company'
    assert client.get('/api/business/dashboard', headers=auth['manager']).json()['scope'] == 'team'
    own_id, other_id = users['employee'].id, users['other_employee'].id
    assert client.get(f'/api/business/employees?user_id={own_id}', headers=auth['manager']).json()['total'] == 1
    assert client.get(f'/api/business/employees?user_id={other_id}', headers=auth['manager']).json()['total'] == 0
    for role in ['employee','candidate']:
        assert client.get('/api/business/dashboard', headers=auth[role]).status_code == 403
        assignment_id=assignments[role].id
        detail=client.get(f'/api/learning/assignments/{assignment_id}',headers=auth[role]).json()
        assert 'correct_answers' not in str(detail['content'])
        progress={'lesson_id':1,'completed':True,'expected_revision':detail['progress_revision'],'operation_key':'mobile-progress-'+role}
        assert client.put(f'/api/learning/assignments/{assignment_id}/progress',headers=auth[role],json=progress).status_code==200
        submitted=client.post(f'/api/learning/assignments/{assignment_id}/responses/test:1/submit',headers=auth[role],json={
            'answer':{'selected':['Greet']},'expected_revision':0,'attempt':1,'submission_key':'mobile-answer-'+role})
        assert submitted.status_code==200,submitted.text
        assert submitted.json()['points']==1
        inbox=client.get('/api/notifications',headers=auth[role]).json()
        assert len(inbox['items'])==1 and inbox['items'][0]['category']=='result'
        peer='candidate' if role=='employee' else 'employee'
        assert client.get(f'/api/notifications/{inbox["items"][0]["id"]}',headers=auth[peer]).status_code==404
        summary=client.get(f'/api/learning/assignments/{assignment_id}/results',headers=auth[role]).json()
        assert summary['status']=='completed' and summary['progress_percent']==100
