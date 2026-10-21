from sqlalchemy import update
import pytest
from app.models.generation_run import GenerationRun
from app.models.user import User
from app.models.learning import OrganizationLearningSettings, LearningAssignment
from app.services.generation_authorization import generation_access_active
from app.services.generation_control import check_cancelled, GenerationCancelled
from test_learning_runtime import world, publish, headers


def test_assignment_rejects_stale_rules_and_freezes_allowed_overrides(client, db_session, world):
    course, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    settings = client.get('/api/team/settings', headers=headers(world['owner'])).json()
    config = db_session.get(OrganizationLearningSettings, world['org'].id)
    config.rules = {**config.rules, 'assignment_overrides': ['max_attempts']}
    config.revision += 1
    db_session.commit()
    path=f'/api/courses/{course.id}/assignments'
    payload={'user_ids':[world['learner'].id], 'expected_rules_revision':settings['revision'], 'rule_overrides':{'max_attempts':5}}
    assert client.post(path, headers=headers(world['owner']), json=payload).status_code == 409
    assert db_session.query(LearningAssignment).count() == 0
    payload['expected_rules_revision'] = config.revision
    assert client.post(path, headers=headers(world['owner']), json={**payload, 'rule_overrides': {'max_attempts': 0}}).status_code == 422
    assert db_session.query(LearningAssignment).count() == 0
    result=client.post(path, headers=headers(world['owner']), json=payload)
    assert result.status_code == 201, result.text
    assignment=db_session.get(LearningAssignment, result.json()['items'][0]['id'])
    assert assignment.rules['max_attempts'] == 5
    config.rules={**config.rules,'max_attempts':1}
    db_session.commit()
    assert assignment.rules['max_attempts'] == 5


def test_employee_learning_attributes_round_trip_and_are_bounded(client, world):
    path=f"/api/team/people/{world['learner'].id}"
    result=client.put(path,headers=headers(world['owner']),json={'position':'Engineer','attributes':{'Qualification':'Senior','Site':'North'}})
    assert result.status_code == 200, result.text
    assert result.json()['attributes'] == {'Qualification':'Senior','Site':'North'}
    assert client.put(path,headers=headers(world['owner']),json={'attributes':{'a':'x'*2001}}).status_code == 422
    assert client.put(path,headers=headers(world['owner']),json={'attributes':{' ':'empty name'}}).status_code == 422
    assert client.put(path,headers=headers(world['owner']),json={'attributes':{'a':'1',' a':'2'}}).status_code == 422
    assert client.put(path,headers=headers(world['learner']),json={'attributes':{'Qualification':'Own promotion'}}).status_code == 403


def test_running_generation_uses_current_access_not_stale_worker_identity(client, db_session, world):
    course, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    author=world['author']
    run=GenerationRun(owner_id=author.id,course_id=course.id,run_type='graph_generation',status='running')
    db_session.add(run)
    db_session.commit()
    assert generation_access_active(db_session,run.id)
    check_cancelled(db_session,run.id)
    db_session.execute(update(User).where(User.id == author.id).values(is_active=False).execution_options(synchronize_session=False))
    assert author.is_active  # Worker still holds the stale in-memory object.
    assert not generation_access_active(db_session,run.id)
    with pytest.raises(GenerationCancelled, match='Доступ автора отозван'):
        check_cancelled(db_session,run.id)
    db_session.execute(update(User).where(User.id == author.id).values(is_active=True,organization_role='member').execution_options(synchronize_session=False))
    assert not generation_access_active(db_session,run.id)
    db_session.execute(update(User).where(User.id == author.id).values(organization_role='author',organization_id=world['other_org'].id).execution_options(synchronize_session=False))
    assert not generation_access_active(db_session,run.id)
