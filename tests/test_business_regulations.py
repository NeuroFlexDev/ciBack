from app.models.course import Course
from app.models.course_generation_settings import CourseGenerationSettings
from app.models.lesson_version import LessonVersion
from tests.test_business_positions import company, headers, make_position


def test_manager_creates_one_document_and_reopens_position(client, db_session, company):
    _, users = company
    manager = users['manager']
    position = make_position(client, manager, 'Администратор')
    base = f"/api/business/positions/{position['id']}"
    assert client.get(base, headers=headers(manager)).json()['name'] == 'Администратор'
    response = client.post(base + '/regulations', headers=headers(manager), json={
        'title': '01. Стандарт сервиса', 'goal': 'Порядок встречи клиента', 'expected_position_revision': 1})
    assert response.status_code == 201, response.text
    course_id = response.json()['course_id']
    settings = db_session.query(CourseGenerationSettings).filter_by(course_id=course_id).one()
    assert settings.lesson_count == 1 and not settings.module_tests_enabled
    assert settings.final_test_enabled
    course = db_session.get(Course, course_id)
    assert course.name == '01. Стандарт сервиса' and course.position_id == position['id']
    materials = client.get(base + '/regulations', headers=headers(manager)).json()['items']
    assert materials[0]['name'] == course.name
    assert materials[0]['content_revision'] == course.content_revision
    assert client.get(base + '/courses', headers=headers(manager)).json()['items'] == materials
    for person in (users['outsider'], users['employee']):
        assert client.get(base, headers=headers(person)).status_code in (403, 404)
        assert client.post(base + '/regulations', headers=headers(person), json={
            'title': 'Чужой документ', 'goal': 'Не создавать', 'expected_position_revision': 1}).status_code in (403, 404)
    assert client.post(base + '/regulations', headers=headers(manager), json={
        'title': 'Конфликт', 'goal': 'Не создавать', 'expected_position_revision': 2}).status_code == 409
    assert db_session.query(Course).count() == 1


def test_regulation_manual_edit_rename_and_stale_wizard_keep_history(client, db_session, company):
    _, users = company
    auth = headers(users['manager'])
    position = make_position(client, users['manager'], 'Старший администратор')
    response = client.post(f"/api/business/positions/{position['id']}/regulations", headers=auth, json={
        'title': '01. Сервис', 'goal': 'Встреча клиента', 'expected_position_revision': 1})
    assert response.status_code == 201
    cid = response.json()['course_id']
    structure = client.get(f'/api/courses/{cid}/structure', headers=auth).json()
    assert structure['position_id'] == position['id']
    module = client.get(f"/api/modules/{structure['modules_timeline'][0]['module_id']}", headers=auth).json()
    assert len(module['lessons']) == 1
    lesson = module['lessons'][0]
    assert lesson['content'] == ''
    saved = client.put(f"/api/lessons/{lesson['id']}", headers=auth, json={'content': '# Встреча клиента\n\nПоздоровайтесь и уточните запрос.', 'expected_revision': 1})
    assert saved.status_code == 200, saved.text
    assert client.put(f"/api/lessons/{lesson['id']}", headers=auth, json={'content': 'Старая вкладка', 'expected_revision': 1}).status_code == 409
    assert db_session.query(LessonVersion).filter_by(lesson_id=lesson['id']).count() == 2
    config = client.get(f'/api/courses/{cid}/generation-settings', headers=auth).json()
    current = client.get(f'/api/courses/{cid}/structure', headers=auth).json()
    renamed = client.put(f'/api/courses/{cid}', headers=auth, json={'title': '02. Стандарт встречи', 'expected_revision': current['content_revision']})
    assert renamed.status_code == 200, renamed.text
    payload = {k: config[k] for k in ('title', 'goal', 'target_audience', 'difficulty', 'language', 'lesson_count', 'module_tests_enabled', 'final_test_enabled', 'approved_plan')}
    payload['expected_revision'] = config['revision']
    assert client.put(f'/api/courses/{cid}/generation-settings', headers=auth, json=payload).status_code == 409
    assert db_session.get(Course, cid).name == '02. Стандарт встречи'
    module = client.get(f"/api/modules/{module['id']}", headers=auth).json()
    assert 'Поздоровайтесь' in module['lessons'][0]['content']
