from datetime import datetime, timedelta
import pytest
from app.models.learning import LearningAssignment, LearningResponse
from app.services.learning_dashboard import dashboard
from test_learning_runtime import world, publish, assign, grant, headers


@pytest.mark.parametrize('organization_kind', ['business', 'government', 'education'])
def test_dashboard_counts_real_scoped_assignments_and_submissions(client, db_session, world, organization_kind):
    world['org'].kind = organization_kind
    if organization_kind == 'business':
        world['author'].organization_role = 'manager'
    db_session.commit()
    course, _, lesson, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    first = assign(client, world, course)
    second = assign(client, world, course, world['second'])
    publish(client, db_session, world['foreign'], assessment=False, practice=False)
    now = datetime(2026, 10, 21, 15)
    row = db_session.get(LearningAssignment, first['id'])
    row.created_at, row.completed_at = now - timedelta(hours=12), now - timedelta(hours=2)
    row.completed_lessons = [lesson.id]
    another = db_session.get(LearningAssignment, second['id'])
    another.created_at = now - timedelta(days=2)
    db_session.add_all([
        LearningResponse(assignment_id=row.id, assessment_id='test:1', attempt=1, status='reviewed', answer={}, submitted_at=now - timedelta(hours=3)),
        LearningResponse(assignment_id=another.id, assessment_id='test:1', attempt=1, status='pending_review', answer={}, submitted_at=now - timedelta(days=1)),
        LearningResponse(assignment_id=row.id, assessment_id='test:2', attempt=1, status='draft', answer={}),
    ])
    db_session.commit()
    result = dashboard(db_session, world['owner'], now=now)
    assert result['scope'] == 'organization'
    assert result['metrics']['courses'] == 1
    assert result['metrics']['completion_percent'] == 50
    assert result['metrics']['lesson_completion_percent'] == 50
    assert result['metrics']['average_completion_hours'] == 10
    assert result['metrics']['submitted_attempts'] == 2
    assert result['metrics']['pending_review'] == 1
    assert len(result['daily']) == 7
    own = dashboard(db_session, world['learner'], now=now)
    assert own['scope'] == 'self'
    assert own['metrics']['completion_percent'] == 100
    assert own['metrics']['submitted_attempts'] == 1
    assert own['metrics']['people'] is None
    assert own['metrics']['pending_review'] == 0
    assert dashboard(db_session, world['owner'], days=1, now=now)['metrics']['completion_percent'] == 100
    unscoped = dashboard(db_session, world['manager'], now=now)
    assert unscoped['scope'] == ('authorized_team' if organization_kind == 'business' else 'self')
    assert unscoped['metrics']['submitted_attempts'] == 0
    if organization_kind == 'business':
        world['second'].manager_id = world['manager'].id
        db_session.commit()
    else:
        grant(client, world, 'manager', 'manager', 'group', _group(client, world, world['second'].id))
    scoped = dashboard(db_session, world['manager'], now=now)
    if organization_kind == 'business':
        assert scoped['scope'] == 'authorized_team'
        assert scoped['metrics']['submitted_attempts'] == 1
        assert scoped['metrics']['completion_percent'] == 0
        assert scoped['metrics']['people'] == 1
    else:
        # Education and government managers retain their explicit group scope.
        assert scoped['scope'] == 'authorized_team'
        assert scoped['metrics']['submitted_attempts'] == 1
        assert scoped['metrics']['completion_percent'] == 0
        assert scoped['metrics']['people'] == 1


def _group(client, world, user_id):
    group = client.post('/api/team/groups', headers=headers(world['owner']), json={'name': 'Team'}).json()
    response = client.post(f"/api/team/groups/{group['id']}/members", headers=headers(world['owner']), json={'user_ids': [user_id]})
    assert response.status_code == 200, response.text
    return group['id']


def test_empty_dashboard_has_no_invented_percentages_and_validates_period(client, db_session, world):
    result = client.get('/api/learning/dashboard', headers=headers(world['learner']))
    assert result.status_code == 200, result.text
    metrics = result.json()['metrics']
    assert metrics['completion_percent'] is None
    assert metrics['average_completion_hours'] is None
    assert metrics['lesson_completion_percent'] is None
    assert metrics['submitted_attempts'] == 0
    assert client.get('/api/learning/dashboard?days=0', headers=headers(world['learner'])).status_code == 422
    assert client.get('/api/learning/dashboard?activity_days=366', headers=headers(world['learner'])).status_code == 422
