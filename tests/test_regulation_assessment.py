from types import SimpleNamespace as N
import pytest
from app.services.regulation_assessment import validate_regulation_assessment, practice_unlocked


def artifact(**changes):
    return N(**({'questions':[N(kind='single_choice') for _ in range(5)], 'practices':[], 'cases':[], 'rubrics':[]} | changes))


def test_basic_regulation_is_automatic_and_legacy_contract_does_not_change():
    validate_regulation_assessment(artifact(), 'test_only')
    with pytest.raises(ValueError):
        validate_regulation_assessment(artifact(practices=[1], rubrics=[1]), 'test_only')
    with pytest.raises(ValueError):
        validate_regulation_assessment(artifact(questions=[N(kind='short_answer')]*5), 'test_only')
    with pytest.raises(ValueError):
        validate_regulation_assessment(artifact(), None)
    validate_regulation_assessment(artifact(practices=[1], cases=[1], rubrics=[1]), None)


def test_advanced_regulation_has_exactly_two_practical_questions():
    validate_regulation_assessment(artifact(practices=[1], cases=[1], rubrics=[1]), 'test_and_practice')
    with pytest.raises(ValueError):
        validate_regulation_assessment(artifact(practices=[1,2], cases=[1], rubrics=[1]), 'test_and_practice')


def test_practice_requires_each_latest_test_pass_but_old_publications_keep_behavior():
    snapshot={'practice_requires_test_pass':True,'assessments':[{'id':'test:1','question_type':'single','max_points':1}, {'id':'task:2','question_type':'open','max_points':1}]}
    assert not practice_unlocked(snapshot, [], 60)
    passed=N(assessment_id='test:1', attempt=1, revision=1, status='reviewed', points=1)
    failed=N(assessment_id='test:1', attempt=2, revision=1, status='reviewed', points=0)
    assert practice_unlocked(snapshot,[passed],60)
    assert not practice_unlocked(snapshot,[failed,passed],60)
    assert practice_unlocked({'assessments':snapshot['assessments']}, [],60)


def test_api_practice_gate_keeps_draft_and_unlocks_after_confirmed_test(client, db_session, world):
    from tests.test_learning_runtime import publish, assign, headers
    from app.models.learning import PublishedLearningContent, LearningResponse
    course, _, _, question, task, version = publish(client, db_session, world['author'])
    published = db_session.get(PublishedLearningContent, version.id)
    published.snapshot = {**published.snapshot, 'practice_requires_test_pass':True}
    db_session.commit()
    assignment=assign(client, world, course)
    path=f'/api/learning/assignments/{assignment["id"]}'
    auth=headers(world['learner'])
    draft={'answer':{'text':'My own durable answer'}, 'attempt':1, 'expected_revision':0}
    assert client.put(f'{path}/responses/task:{task.id}',headers=auth,json=draft).status_code==200
    send={**draft,'expected_revision':1,'submission_key':'practice-before-test-001'}
    blocked=client.post(f'{path}/responses/task:{task.id}/submit',headers=auth,json=send)
    assert blocked.status_code==409
    db_session.expire_all()
    row=db_session.query(LearningResponse).filter_by(assignment_id=assignment['id'], assessment_id=f'task:{task.id}').one()
    assert row.status=='draft' and row.answer==draft['answer'] and row.revision==1
    data=client.get(path,headers=auth).json()
    assert next(a for a in data['content']['modules'][0]['assessments'] if a['id']==f'task:{task.id}')['locked_reason']
    correct={'answer':{'selected':['Correct']},'attempt':1,'expected_revision':0,'submission_key':'closed-test-confirmed-001'}
    assert client.post(f'{path}/responses/test:{question.id}/submit',headers=auth,json=correct).status_code==200
    data=client.get(path,headers=auth).json()
    assert 'locked_reason' not in next(a for a in data['content']['modules'][0]['assessments'] if a['id']==f'task:{task.id}')
    sent=client.post(f'{path}/responses/task:{task.id}/submit',headers=auth,json=send)
    assert sent.status_code==200 and sent.json()['status']=='pending_review'
    again=client.post(f'{path}/responses/task:{task.id}/submit',headers=auth,json=send)
    assert again.json()['id']==sent.json()['id']


from tests.test_learning_runtime import world  # noqa: E402,F401 — shared tenant-isolation fixture
