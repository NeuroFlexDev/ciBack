from app.models.administration import AdminEvent
from app.models.user import User
from test_learning_runtime import world, headers


def test_resource_profile_is_neutral_versioned_metadata_and_never_changes_capacity(client, db_session, world):
    operator = User(email='resource-profile@platform.test', password_hash='unused', is_admin=True)
    db_session.add(operator); db_session.commit()
    org=world['org']
    original_limits=(org.seat_limit,org.course_limit,org.source_limit,org.generation_limit)
    path=f'/api/admin/organizations/{org.id}/delivery'
    current=client.get(path,headers=headers(operator)).json()
    payload={'expected_revision':current['revision'],'features':current['features'],
        'delivery':{**current['delivery'],'resource_profile':'team'}}
    assert client.put(path,headers=headers(world['owner']),json=payload).status_code==403
    assert client.put(path,headers=headers(operator),json={**payload,'delivery':{'resource_profile':'unlimited'}}).status_code==422
    saved=client.put(path,headers=headers(operator),json=payload)
    assert saved.status_code==200,saved.text
    assert saved.json()['delivery']['resource_profile']=='team'
    assert saved.json()['profile']==current['profile']
    assert saved.json()['rules']==current['rules']
    assert (org.seat_limit,org.course_limit,org.source_limit,org.generation_limit)==original_limits
    assert client.put(path,headers=headers(operator),json=payload).status_code==409
    own=client.get('/api/team/settings',headers=headers(world['admin'])).json()
    assert own['delivery']['resource_profile']=='team'
    foreign=client.get('/api/team/settings',headers=headers(world['foreign'])).json()
    assert 'resource_profile' not in foreign['delivery']
    assert db_session.query(AdminEvent).filter_by(action='organization.delivery_changed').count()==1
