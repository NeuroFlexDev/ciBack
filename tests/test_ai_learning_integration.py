import json
from app.models.learning import LearningResponse
from app.models.materials import CourseImage
from app.services.learning_service import settings_for
from tests.test_learning_runtime import world, headers, publish, assign, grant
from tests.test_materials_release import png


def test_coauthor_images_keep_tenant_after_owner_transfer(client, db_session, world, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.material_quota import used_disk_bytes
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, *_ = publish(client, db_session, world['author'])
    uploaded = client.post(f'/api/courses/{course.id}/images', headers=headers(world['author']), files={'file': ('image.png', png(), 'image/png')})
    assert uploaded.status_code == 201
    item = uploaded.json()
    grant(client, world, 'second', 'author', 'course', course.id)
    assert client.get(item['content_url'], headers=headers(world['second'])).status_code == 200
    assert client.post(f'/api/images/{item["id"]}/review', headers=headers(world['second']), json={'decision': 'approved'}).status_code == 200
    world['author'].organization_id = world['other_org'].id
    db_session.commit()
    assert client.get(item['content_url'], headers=headers(world['author'])).status_code == 404
    assert used_disk_bytes(db_session, [], organization_id=world['org'].id) >= item['size_bytes']
    assert used_disk_bytes(db_session, [], organization_id=world['other_org'].id) == 0
    config = settings_for(db_session, world['org'].id)
    config.features = {**config.features, 'images': False, 'assistant': False}; db_session.commit()
    assert client.get(item['content_url'], headers=headers(world['second'])).status_code == 200
    assert client.get(f'/api/courses/{course.id}/images', headers=headers(world['second'])).status_code == 200
    assert client.post(f'/api/courses/{course.id}/images', headers=headers(world['second']), files={'file': ('image.png', png(), 'image/png')}).status_code == 403
    assert client.post(f'/api/courses/{course.id}/images/generate', headers=headers(world['second']), json={'prompt': 'Blocked generator', 'request_id': 'feature-block-123'}).status_code == 403
    chat = client.post('/api/chat/', headers=headers(world['second']), json={'name': 'Disabled AI', 'course_id': course.id}).json()
    assert client.post('/api/chat/send', headers=headers(world['second']), json={'chat_id': chat['id'], 'text': 'No AI execution', 'request_id': 'disabled-ai-123'}).status_code == 403


def test_ai_grade_proposal_is_scoped_idempotent_and_never_final(client, db_session, world, monkeypatch):
    course, _, _, _, task, _ = publish(client, db_session, world['author'], assessment=False)
    metadata = {'question_type': 'open', 'grading_mode': 'ai_assisted', 'max_points': 4,
                'criteria': [{'id': 'accuracy', 'title': 'Accuracy', 'max_points': 4}], 'expected_revision': 1}
    assert client.put(f'/api/tasks/{task.id}/assessment-settings', headers=headers(world['author']), json=metadata).status_code == 200
    assert client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author'])).status_code == 200
    config = settings_for(db_session, world['org'].id)
    config.features = {**config.features, 'ai_grading': True}
    config.rules = {**config.rules, 'allow_ai_grading': True}
    db_session.commit()
    assignment = assign(client, world, course)
    submitted = client.post(f'/api/learning/assignments/{assignment["id"]}/responses/task:{task.id}/submit', headers=headers(world['learner']),
        json={'answer': {'text': 'Evidence'}, 'submission_key': 'ai-review-submit'}).json()
    grant(client, world, 'reviewer', 'reviewer', 'course', course.id)
    path = f'/api/learning/reviews/{submitted["id"]}/ai-proposal'
    request = {'request_id': 'proposal-request-123', 'expected_revision': submitted['revision']}
    calls = []
    def invoke(*args, **kwargs):
        calls.append(args)
        return {'text': json.dumps({'points': 3, 'feedback': 'Three points supported by response.', 'criteria_scores': [{'id': 'accuracy', 'points': 3}]}), 'model': 'reviewed-model'}
    monkeypatch.setattr('app.ai.gateway.invoke_messages', invoke)
    assert client.post(path, headers=headers(world['learner']), json=request).status_code == 404
    result = client.post(path, headers=headers(world['reviewer']), json=request)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'completed' and result.json()['points'] == 3
    assert client.post(path, headers=headers(world['reviewer']), json=request).json() == result.json()
    assert len(calls) == 1
    response = db_session.get(LearningResponse, submitted['id'])
    assert response.status == 'pending_review' and response.points is None
    review = client.get(f'/api/learning/reviews/{response.id}', headers=headers(world['reviewer'])).json()
    assert review['grading_metadata']['ai_proposal']['status'] == 'completed'
    results = client.get(f'/api/learning/assignments/{assignment["id"]}/results', headers=headers(world['learner'])).json()
    assert 'ai_proposal' not in json.dumps(results)
    config.features = {**config.features, 'ai_grading': False}; db_session.commit()
    assert client.post(path, headers=headers(world['reviewer']), json={**request, 'request_id': 'new-proposal-key'}).status_code == 403


def test_independent_reviewer_can_inspect_and_approve_draft_image(client, db_session, world, monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, *_ = publish(client, db_session, world['author'])
    uploaded = client.post(f'/api/courses/{course.id}/images', headers=headers(world['author']), files={'file': ('image.png', png(), 'image/png')}).json()
    grant(client, world, 'reviewer', 'reviewer', 'course', course.id)
    config = settings_for(db_session, world['org'].id)
    config.rules = {**config.rules, 'require_independent_approval': True}; db_session.commit()
    assert client.get(uploaded['content_url'], headers=headers(world['reviewer'])).status_code == 200
    library = client.get(f'/api/courses/{course.id}/images', headers=headers(world['reviewer']))
    assert library.status_code == 200 and library.json()['items'][0]['can_approve'] is True
    assert library.json()['can_upload'] is False and library.json()['can_generate'] is False
    author_library = client.get(f'/api/courses/{course.id}/images', headers=headers(world['author'])).json()
    assert author_library['items'][0]['can_approve'] is False
    assert client.get(f'/api/courses/{course.id}/images', headers=headers(world['foreign'])).status_code == 404
    path = f'/api/images/{uploaded["id"]}/review'
    assert client.post(path, headers=headers(world['author']), json={'decision': 'approved'}).status_code == 403
    assert client.post(path, headers=headers(world['reviewer']), json={'decision': 'approved'}).status_code == 200
