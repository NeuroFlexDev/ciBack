import json
from copy import deepcopy

from app.models.learning import PublishedLearningContent, LearningAssignment
from app.services.learning_service import public_snapshot, assessment_for
from app.services.learner_assistant_service import assignment_context
from tests.test_learning_runtime import world, headers, publish, assign


def test_legacy_private_case_suffix_is_hidden_without_rewriting_snapshot(client, db_session, world):
    course, _, _, _, task, version = publish(client, db_session, world['author'])
    published = db_session.get(PublishedLearningContent, version.id)
    data = deepcopy(published.snapshot)
    case = next(item for item in data['modules'][0]['assessments'] if item['id'] == f'task:{task.id}')
    case['description'] = 'Real scenario\n\nExplain your reasoning\n\nОжидаемый ответ: Private historical exemplar\nwith a second line'
    published.snapshot = data  # Fixture models a version published by the old implementation.
    db_session.commit()
    assignment = assign(client, world, course)
    row = db_session.get(LearningAssignment, assignment['id'])
    public = public_snapshot(db_session, row)
    assert 'Private historical exemplar' not in json.dumps(public)
    assert public['modules'][0]['assessments'][-1]['description'] == 'Real scenario\n\nExplain your reasoning'
    endpoint = client.get(f'/api/learning/assignments/{row.id}', headers=headers(world['learner']))
    assert endpoint.status_code == 200 and 'Private historical exemplar' not in endpoint.text
    _, assistant = assignment_context(db_session, world['learner'], row.id)
    assert 'Private historical exemplar' not in json.dumps(assistant)
    assert 'Private historical exemplar' in assessment_for(db_session, row, f'task:{task.id}')['description']
    db_session.expire_all()
    assert db_session.get(PublishedLearningContent, version.id).snapshot == data
