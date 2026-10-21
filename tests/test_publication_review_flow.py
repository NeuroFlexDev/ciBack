from app.models.learning import LearningPublicationApproval
from test_learning_runtime import world, headers, publish, grant


def test_review_queue_and_history_are_scoped_and_stale_approval_cannot_publish(client, db_session, world):
    course, _, lesson, _, _, _ = publish(client, db_session, world['author'], practice=False)
    other, *_ = publish(client, db_session, world['foreign'], practice=False)
    reviewer = headers(world['reviewer'])
    assert client.get('/api/learning/course-reviews', headers=reviewer).json()['items'] == []
    grant(client, world, 'reviewer', 'reviewer', 'course', course.id)
    rows = client.get('/api/learning/course-reviews?limit=1', headers=reviewer).json()
    assert [row['id'] for row in rows['items']] == [course.id]
    assert client.get(f'/api/courses/{other.id}/approvals', headers=reviewer).status_code == 404
    path = f'/api/courses/{course.id}'
    assert client.get(path + '/approvals', headers=headers(world['owner'])).status_code == 404
    content = client.get(path + '/review-content', headers=reviewer).json()
    decision = {'content_digest': content['content_digest'], 'decision': 'approved', 'comment': 'Checked original version'}
    assert client.put(f'/api/lessons/{lesson.id}', headers=headers(world['author']), json={
        'content': 'Changed after review began', 'expected_revision': lesson.revision}).status_code == 200
    assert client.post(path + '/approvals', headers=reviewer, json=decision).status_code == 409
    assert db_session.query(LearningPublicationApproval).count() == 0
    current = client.get(path + '/review-content', headers=reviewer).json()
    decision['content_digest'] = current['content_digest']
    assert client.post(path + '/approvals', headers=reviewer, json=decision).status_code == 201
    for actor in ['author', 'reviewer']:
        history = client.get(path + '/approvals', headers=headers(world[actor])).json()
        assert history['content_digest'] == current['content_digest']
        assert history['items'][0]['comment'] == decision['comment']
    course.is_deleted = True
    db_session.commit()
    assert client.get('/api/learning/course-reviews', headers=reviewer).json()['items'] == []
    assert client.get(path + '/review-content', headers=reviewer).status_code == 403


def test_rubric_levels_round_trip_and_cannot_exceed_criterion_scale(client, db_session, world):
    _, _, _, _, task, _ = publish(client, db_session, world['author'])
    path = f'/api/tasks/{task.id}/assessment-settings'
    author = headers(world['author'])
    config = client.get(path, headers=author).json()
    payload = {key: config[key] for key in ['question_type', 'correct_answers', 'max_points', 'criteria', 'grading_mode']}
    payload['expected_revision'] = config['revision']
    payload['criteria'][0]['levels'] = [{'title': 'Complete', 'description': 'All steps explained', 'points': 1}]
    saved = client.put(path, headers=author, json=payload)
    assert saved.status_code == 200, saved.text
    assert client.get(path, headers=author).json()['criteria'][0]['levels'] == payload['criteria'][0]['levels']
    payload['expected_revision'] = saved.json()['revision']
    payload['criteria'][0]['levels'][0]['points'] = 2
    assert client.put(path, headers=author, json=payload).status_code == 422
    assert client.get(path, headers=author).json()['criteria'][0]['levels'][0]['points'] == 1
