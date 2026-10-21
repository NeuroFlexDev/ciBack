import json
import pytest
from sqlalchemy import update
from fastapi import HTTPException
from app.models.user import User
from app.models.learning import LearningAssignment, LearningRoleGrant, LearningResponse
from app.models.materials import LearnerAssistantMessage, ScopedAIUsage
from app.models.ai_resources import AIResourceUsage
from app.models.chat import ChatMessage
from app.services import learner_assistant_service
from app.services.learning_service import settings_for
from tests.test_learning_runtime import world, headers, publish, assign, grant


@pytest.mark.parametrize('revoke', ['assignment', 'membership', 'feature'])
def test_learner_result_is_withheld_after_access_changes_during_provider_wait(client, db_session, world, monkeypatch, revoke):
    course, _, lesson, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    assignment = assign(client, world, course)
    def invoke(*args, **kwargs):
        if revoke == 'assignment':
            db_session.execute(update(LearningAssignment).where(LearningAssignment.id == assignment['id']).values(state='revoked'))
        elif revoke == 'membership':
            db_session.execute(update(User).where(User.id == world['learner'].id).values(organization_id=world['other_org'].id).execution_options(synchronize_session=False))
        else:
            config = settings_for(db_session, world['org'].id)
            config.features = {**config.features, 'assistant': False}
        db_session.commit()
        return {'text': json.dumps({'answer': 'Withheld private lesson explanation', 'cited_lesson_ids': [lesson.id]}), 'model': 'test', 'usage': {'input_tokens': 10, 'output_tokens': 10}}
    monkeypatch.setattr('app.ai.gateway.invoke_messages', invoke)
    with pytest.raises(HTTPException) as denied:
        learner_assistant_service.send_message(db_session, world['learner'], assignment['id'], lesson.id, 'Explain this lesson', 'revoked-request-123')
    assert denied.value.status_code == 403
    receipt = db_session.query(LearnerAssistantMessage).one()
    assert receipt.question == 'Explain this lesson' and receipt.answer is None
    assert receipt.error_code == 'assistant_access_revoked'
    assert db_session.query(AIResourceUsage).one().status == 'completed'


def test_author_chat_does_not_persist_or_return_ai_answer_after_role_revocation(client, db_session, world, monkeypatch):
    course, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    chat = client.post('/api/chat/', headers=headers(world['author']), json={'name': 'Scoped', 'course_id': course.id}).json()
    def invoke(*args, **kwargs):
        db_session.execute(update(User).where(User.id == world['author'].id).values(organization_role='member'))
        db_session.query(LearningRoleGrant).filter_by(user_id=world['author'].id).delete(synchronize_session=False)
        db_session.commit()
        return {'text': 'Withheld course data', 'model': 'test', 'usage': {'input_tokens': 10, 'output_tokens': 10}}
    monkeypatch.setattr('app.ai.gateway.invoke_messages', invoke)
    result = client.post('/api/chat/send', headers=headers(world['author']), json={'chat_id': chat['id'], 'text': 'Explain course', 'request_id': 'revoked-author-123'})
    assert result.status_code == 403, result.text
    assert 'Withheld course data' not in result.text
    assert db_session.query(ChatMessage).filter_by(role='assistant').count() == 0
    question = db_session.query(ChatMessage).filter_by(role='user').one()
    assert question.message_metadata['request_status'] == 'failed' and question.content == 'Explain course'
    assert db_session.query(ScopedAIUsage).one().status == 'completed'
    assert db_session.query(AIResourceUsage).one().status == 'completed'


def test_reviewer_revoked_while_grading_cannot_receive_proposal(client, db_session, world, monkeypatch):
    course, _, _, _, task, _ = publish(client, db_session, world['author'], assessment=False)
    from app.models.learning import AssessmentDefinition
    current = db_session.query(AssessmentDefinition).filter_by(task_id=task.id).first()
    metadata = {'question_type': 'open', 'grading_mode': 'ai_assisted', 'max_points': 4,
        'criteria': [{'id': 'a', 'title': 'Accuracy', 'max_points': 4}], 'expected_revision': current.revision if current else 0}
    assert client.put(f'/api/tasks/{task.id}/assessment-settings', headers=headers(world['author']), json=metadata).status_code == 200
    assert client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author'])).status_code == 200
    config = settings_for(db_session, world['org'].id)
    config.features = {**config.features, 'ai_grading': True}; config.rules = {**config.rules, 'allow_ai_grading': True}; db_session.commit()
    assignment = assign(client, world, course)
    submitted = client.post(f'/api/learning/assignments/{assignment["id"]}/responses/task:{task.id}/submit', headers=headers(world['learner']),
        json={'answer': {'text': 'Evidence'}, 'submission_key': 'revoked-review-answer'}).json()
    grant(client, world, 'reviewer', 'reviewer', 'course', course.id)
    def invoke(*args, **kwargs):
        db_session.query(LearningRoleGrant).filter_by(user_id=world['reviewer'].id).delete(synchronize_session=False)
        world['reviewer'].organization_role = 'member'; db_session.commit()
        return {'text': json.dumps({'points': 3, 'feedback': 'Withheld feedback', 'criteria_scores': [{'id': 'a', 'points': 3, 'rationale': 'Partial evidence', 'evidence': ['Evidence']}]}), 'model': 'test'}
    monkeypatch.setattr('app.ai.gateway.invoke_messages', invoke)
    result = client.post(f'/api/learning/reviews/{submitted["id"]}/ai-proposal', headers=headers(world['reviewer']), json={'request_id': 'revoked-review-123', 'expected_revision': submitted['revision']})
    assert result.status_code == 403 and 'Withheld feedback' not in result.text
    response = db_session.get(LearningResponse, submitted['id'])
    assert response.status == 'pending_review' and response.points is None
    assert response.grading_metadata['ai_proposal']['error_code'] == 'ai_access_revoked'
    assert 'criteria_scores' not in response.grading_metadata['ai_proposal']
    assert db_session.query(AIResourceUsage).one().status == 'completed'


@pytest.mark.parametrize('when', ['before', 'during'])
def test_queued_image_rechecks_access_and_only_releases_proven_unsent_cost(client, db_session, world, monkeypatch, tmp_path, when):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.core.config import settings, AIProvider
    from app.models.materials import CourseImage
    from app.services.image_service import execute_image_generation
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(settings, 'AI_PROVIDERS', [AIProvider(id='images', base_url='https://example.invalid/v1')])
    monkeypatch.setattr(settings, 'IMAGE_PROVIDER', 'images')
    monkeypatch.setattr(settings, 'IMAGE_MODEL', 'test-image')
    monkeypatch.setattr(settings, 'IMAGE_PRICE_RUB', Decimal('2'))
    monkeypatch.setattr('app.ai.providers.available', lambda _: True)
    monkeypatch.setattr('app.ai.providers.credential', lambda _: 'test-only-no-network')
    monkeypatch.setattr('app.services.image_service.enqueue_image', lambda _: None)
    course, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    result = client.post(f'/api/courses/{course.id}/images/generate', headers=headers(world['author']), json={'request_id': 'revoked-image-123', 'prompt': 'Create a diagram'})
    assert result.status_code == 202, result.text
    item_id = result.json()['id']
    def revoke():
        world['author'].organization_role = 'member'
        db_session.query(LearningRoleGrant).filter_by(user_id=world['author'].id).delete(synchronize_session=False)
        db_session.commit()
    calls = []
    def generate(**kwargs):
        calls.append(True)
        revoke()
        return SimpleNamespace(data=[SimpleNamespace(b64_json='not-decoded-after-revocation')])
    if when == 'before': revoke()
    monkeypatch.setattr('openai.OpenAI', lambda **kwargs: SimpleNamespace(images=SimpleNamespace(generate=generate)))
    monkeypatch.setattr('app.database.db.SessionLocal', lambda: db_session)
    monkeypatch.setattr(db_session, 'close', lambda: None)
    execute_image_generation(item_id)
    item = db_session.get(CourseImage, item_id)
    usage = db_session.query(AIResourceUsage).filter_by(key=f'image:{item_id}').one()
    assert item.status == 'failed' and item.error_code == 'image_access_revoked' and item.storage_key is None
    assert calls == ([] if when == 'before' else [True])
    assert usage.status == ('unavailable' if when == 'before' else 'unknown')
    assert usage.cost_rub == (0 if when == 'before' else Decimal('2'))


def test_assigned_image_metadata_never_exposes_teacher_prompt_or_review(client, db_session, world, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.models.materials import CourseImage
    from tests.test_materials_release import png
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    course, _, lesson, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    uploaded = client.post(f'/api/courses/{course.id}/images', headers=headers(world['author']), files={'file': ('diagram.png', png(), 'image/png')}).json()
    item = db_session.get(CourseImage, uploaded['id'])
    item.prompt = 'PRIVATE answer key for the teacher'; db_session.commit()
    assert client.post(f'/api/images/{item.id}/review', headers=headers(world['author']), json={'decision': 'approved', 'comment': 'PRIVATE reviewer note'}).status_code == 200
    assert client.put(f'/api/lessons/{lesson.id}', headers=headers(world['author']), json={'content': f'![Diagram](/api/images/{item.id}/content)', 'expected_revision': lesson.revision}).status_code == 200
    assert client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author'])).status_code == 200
    assign(client, world, course)
    result = client.get(f'/api/images/{item.id}', headers=headers(world['learner']))
    assert result.status_code == 200 and result.json()['content_url']
    assert 'PRIVATE' not in result.text and 'prompt' not in result.json() and 'review_comment' not in result.json()
    assert client.get(f'/api/images/{item.id}', headers=headers(world['author'])).json()['prompt'] == item.prompt
