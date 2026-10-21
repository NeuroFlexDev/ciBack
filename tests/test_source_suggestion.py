from app.models.document import DocumentChunk
from tests.test_upload_pipeline import _course_and_document


def ready_document(db_session, auth_user):
    course, document, _ = _course_and_document(db_session, auth_user)
    document.status = 'indexed'
    document.chunks.append(DocumentChunk(document_version=1, chunk_index=0,
        section='Адаптация менеджеров', text='Адаптация менеджеров\nСотрудники изучат продукт и научатся отвечать на вопросы клиентов. Затем они отработают работу с возражениями.'))
    db_session.commit()
    return course, document


def test_extracts_editable_metadata_without_saving_or_provider(client, db_session, auth_user, auth_headers):
    course, document = ready_document(db_session, auth_user)
    response = client.post(f'/api/courses/{course.id}/source-suggestion', headers=auth_headers,
                           json={'document_ids':[document.id]})
    assert response.status_code == 200
    result = response.json()
    assert result['title'] == 'Адаптация менеджеров'
    assert result['goal'].startswith('Сотрудники изучат продукт')
    assert result['sources'][0]['version'] == 1
    db_session.refresh(course)
    assert course.name == 'Pipeline course'


def test_foreign_course_and_unready_or_archived_sources_not_exposed(client, db_session, auth_user, auth_headers):
    course, document = ready_document(db_session, auth_user)
    from app.models.user import User
    from app.core.security import create_access_token
    stranger = User(email='other-suggestion@example.test', password_hash='unused')
    db_session.add(stranger); db_session.commit()
    endpoint=f'/api/courses/{course.id}/source-suggestion'
    body={'document_ids':[document.id]}
    assert client.post(endpoint, headers={'Authorization':'Bearer '+create_access_token(stranger.id)}, json=body).status_code == 404
    document.status = 'processing'; db_session.commit()
    assert client.post(endpoint, headers=auth_headers, json=body).status_code == 422
    document.status = 'indexed'; document.is_current = False; db_session.commit()
    assert client.post(endpoint, headers=auth_headers, json=body).status_code == 422


def test_old_deleted_chunks_are_excluded_and_lengths_bounded(db_session, auth_user):
    from app.services.source_suggestion import source_suggestion
    _, document = ready_document(db_session, auth_user)
    document.chunks[0].is_deleted = True
    document.chunks.append(DocumentChunk(document_version=1, chunk_index=1, section='Новая тема', text='Новый материал. ' * 1000))
    result=source_suggestion([document])
    assert result['title'] == 'Новая тема'
    assert 'Сотрудники изучат' not in result['goal']
    assert len(result['title']) <= 200 and len(result['goal']) <= 1800
