import json

from app.models.generation_run import GenerationRun
from app.services.learning_service import settings_for
from app.services.organization_ai_context import organization_ai_context
from app.services.scoped_ai import tracked_completion
from app.services.generation_service import render_prompt
from app.services import learner_assistant_service as assistant
from tests.test_learning_runtime import world, headers, publish, assign


def configure(db, world):
    config = settings_for(db, world['org'].id)
    config.ai_context = {'audience': 'New mechanics', 'industry': 'Railways', 'terminology': 'Use depot terminology',
                         'specialist_role': 'Technical trainer', 'goals': 'Safe maintenance', 'constraints': 'No live-voltage practice',
                         'unknown_secret': 'must never enter prompts'}
    other = settings_for(db, world['other_org'].id)
    other.ai_context = {'industry': 'foreign confidential domain'}
    db.commit()
    return config


def test_interactive_context_is_tenant_scoped_and_preserves_original_contract(db_session, world):
    configure(db_session, world)
    captured = []
    original = [{'role': 'system', 'content': 'Only use supplied course sources.'}, {'role': 'user', 'content': 'Explain bearings'}]
    tracked_completion(db_session, world['author'].id, None, 'canvas', original,
        invoke=lambda role, messages, **kwargs: captured.append(messages) or {'text': 'Grounded response'})
    text = captured[0][0]['content']
    assert text.startswith(original[0]['content'])
    assert all(value in text for value in ['New mechanics', 'Railways', 'Technical trainer', 'Safe maintenance', 'No live-voltage practice'])
    assert 'do not invent' in text.lower() and 'override' in text
    assert 'foreign confidential' not in text and 'must never enter' not in text
    assert original[0]['content'] == 'Only use supplied course sources.'
    assert organization_ai_context(db_session, None) == {}


def test_queued_generation_freezes_profile_and_renders_it(client, db_session, world, monkeypatch):
    from tests.test_async_generation_launch import _settings, _document
    config = configure(db_session, world)
    course, *_ = publish(client, db_session, world['author'])
    document = _document(db_session, course, world['author'])
    monkeypatch.setattr('app.routes.pipeline.enqueue_generation', lambda run_id: None)
    result = client.post(f'/api/courses/{course.id}/generation-runs', headers=headers(world['author']),
                        json={'settings': _settings(), 'document_ids': [document.id], 'request_id': 'context-freeze-key'})
    assert result.status_code == 202, result.text
    run = db_session.get(GenerationRun, result.json()['run_id'])
    config.ai_context = {'industry': 'Later changed industry'}; db_session.commit()
    repeated = client.post(f'/api/courses/{course.id}/generation-runs', headers=headers(world['author']),
                           json={'settings': _settings(), 'document_ids': [document.id], 'request_id': 'context-freeze-key'})
    assert repeated.status_code == 202 and repeated.json()['run_id'] == run.id
    frozen = run.settings_snapshot['organization_context']
    assert frozen['industry'] == 'Railways'
    prompt = render_prompt('ingestion_agent_prompt.j2', course_title='Bearings', source_catalog_json='[]', organization_context=frozen)
    assert 'Railways' in prompt and 'Later changed industry' not in prompt


def test_learner_context_has_preferences_without_answer_keys(client, db_session, world, monkeypatch):
    configure(db_session, world)
    course, _, lesson, *_ = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    captured = []
    def generate(role, messages, **kwargs):
        captured.append(messages)
        return {'text': json.dumps({'answer': 'Explanation from lesson', 'cited_lesson_ids': [lesson.id]}), 'model': 'test'}
    monkeypatch.setattr('app.ai.gateway.invoke_messages', generate)
    result = assistant.send_message(db_session, world['learner'], assignment['id'], lesson.id, 'Explain', 'learner-org-context')
    assert result.status == 'completed'
    prompt = json.dumps(captured)
    assert 'Railways' in prompt and 'original published lesson' in prompt.lower()
    assert 'correct_answer' not in prompt and 'foreign confidential' not in prompt
