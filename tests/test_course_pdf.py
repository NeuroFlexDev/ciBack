from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import update
from app.models.administration import AdminEvent
from app.models.learning import PublishedLearningContent
from app.models.materials import CourseImage
from app.models.user import User
from app.services import image_service, course_pdf
from app.services.pdf_assets import diagram_key, safe_svg, PDFRenderError
from app.services.pdf_document import build_pdf
from tests.test_learning_runtime import world, publish, headers
from tests.test_course_transfer import selection, BASE

SOURCE = 'flowchart LR\n A[Изучить] --> B[Проверить]'
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="450" height="100" viewBox="0 0 450 100"><g fill="#e9f2d2" stroke="#526b15" stroke-width="1"><rect x="5" y="20" width="175" height="60" rx="10"/><rect x="270" y="20" width="175" height="60" rx="10"/><path d="M180 50 H260" fill="none"/><polygon points="260,45 270,50 260,55" fill="#526b15"/></g><g font-size="18" fill="#141414"><text x="48" y="57">Изучить</text><text x="303" y="57">Проверить</text></g></svg>'''


def fixture_content():
    rows = '\n'.join(f'| {i+1} | Содержательный пример применения правила и его объяснение на русском языке. | {i+1}/32 |' for i in range(32))
    body = r'''## От идеи к результату
Обучение объединяет **теорию**, *практику* и обратную связь.

![Фирменный визуальный ритм Lernium](images/illustration.png)

### Математическая модель
Формула $E=mc^2$ связывает энергию и массу. Для средней оценки используйте:

$$
\bar{x}=\frac{1}{n}\sum_{i=1}^{n}x_i,\quad \int_0^1 x^2\,dx=\frac{1}{3}
$$

> Сначала разберитесь в принципе, затем примените его в своей задаче.

1. Выберите цель.
2. Соберите исходные данные.
3. Оцените результат.

```mermaid
''' + SOURCE + '''
```

| Шаг | Содержание и пояснение | Прогресс |
| --- | --- | --- |
''' + rows + '''

```python
# Проверяем данные без потери исходного ответа
result = sum(values) / len(values)
```
'''
    return {'course': {'title': 'Как устроено эффективное обучение', 'description': 'Практическое руководство: от цели до измеримого результата. **Проверочный образец PDF** с кириллицей, схемой, иллюстрацией, формулами и таблицей.'},
        'modules': [{'title': 'Понимание и применение', 'description': 'Постройте ясную модель задачи.', 'lessons': [{'title': 'Исследуем материал', 'content': body}]},
            {'title': 'Проверка результата', 'lessons': [{'title': 'Обратная связь и улучшения', 'content': 'Сравните результат с критерием. Обсудите расхождения и повторите практику.\n\n- Чёткая цель\n- Проверяемые данные\n- Следующий шаг'}]}],
        'assessments': [{'question': 'Как проверить, что цель достигнута?', 'answers': ['По измеримому критерию', 'Только по впечатлению'], 'max_points': 2}]}


def image_bytes():
    from PIL import ImageDraw
    picture = Image.new('RGB', (960, 480), '#141414')
    draw = ImageDraw.Draw(picture)
    for i in range(15):
        draw.line([(20+i*66, 100), (70+i*66, 350)], fill='#caf432', width=16)
    output = BytesIO(); picture.save(output, 'PNG')
    return output.getvalue()


def sample_pdf():
    return build_pdf(fixture_content(), {'images/illustration.png': image_bytes()},
        {'revision': 3, 'published_at': '2026-10-22', 'snapshot_sha256': 'a'*64, 'includes_answer_keys': False},
        {diagram_key('mermaid', SOURCE): SVG})


def test_real_branded_pdf_embeds_cyrillic_fonts_math_images_table_headers_and_outlines():
    # Node/MathJax is required in CI; missing dependencies must fail this test.
    data = sample_pdf()
    assert data.startswith(b'%PDF-')
    document = PdfReader(BytesIO(data))
    text = '\n'.join(page.extract_text() for page in document.pages)
    assert 'Как устроено эффективное обучение' in text
    assert 'Изучить' in text and 'Проверить' in text
    assert '■' not in text
    assert '32/32' in text and 'result = sum(values)' in text
    assert text.count('Содержание и пояснение') >= 2  # repeated header after table split
    assert 'Placeholder' not in text and 'Содержание / Contents' in text
    assert len(document.outline) >= 3
    assert sum(len(page.images) for page in document.pages) >= 3
    assert document.metadata.title == fixture_content()['course']['title']


def test_pdf_owner_consent_tenant_exact_snapshot_and_private_answers(client, db_session, world):
    course, _, _, _, _, version = publish(client, db_session, world['author'])
    payload = selection(client, world, version.id)
    for actor in [world['author'], world['admin'], world['learner']]:
        assert client.post(BASE+'/pdf-plan', headers=headers(actor), json=payload).status_code == 403
        assert client.post(BASE+'/export.pdf', headers=headers(actor), json=payload).status_code == 403
    world['foreign'].organization_role = 'owner'; db_session.commit()
    assert client.post(BASE+'/export.pdf', headers=headers(world['foreign']), json=payload).status_code == 404
    assert client.post(BASE+'/pdf-plan', headers=headers(world['owner']), json={**payload, 'confirm_transfer': False}).status_code == 422
    assert client.post(BASE+'/export.pdf', headers=headers(world['owner']), json={**payload, 'snapshot_sha256': '0'*64}).status_code == 409
    assert client.post(BASE+'/export.pdf', headers=headers(world['owner']), json={**payload, 'include_answer_keys': True}).status_code == 422
    response = client.post(BASE+'/export.pdf', headers=headers(world['owner']), json=payload)
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert 'no-store' in response.headers['cache-control']
    reader = PdfReader(BytesIO(response.content))
    assert 'Instructor answer key' not in '\n'.join(page.extract_text() for page in reader.pages)
    response = client.post(BASE+'/export.pdf', headers=headers(world['owner']), json={**payload, 'include_answer_keys': True, 'confirm_private_materials': True})
    assert response.status_code == 200, response.text
    assert 'Instructor answer key' in '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert db_session.query(AdminEvent).filter_by(action='learning.course_pdf_exported').count() == 2
    assert db_session.query(AdminEvent).filter_by(action='learning.course_export_prepared').count() == 0
    listing = client.get(BASE+f'?course_id={course.id}', headers=headers(world['owner'])).json()
    assert listing['total'] == 1
    assert client.get(BASE+'?course_id=99999', headers=headers(world['owner'])).json()['total'] == 0


def test_pdf_plan_exact_diagrams_required_and_unsafe_svg_never_returns_partial_pdf(client, db_session, world):
    _, _, _, _, _, version = publish(client, db_session, world['author'])
    row = db_session.get(PublishedLearningContent, version.id)
    snapshot = deepcopy(row.snapshot)
    snapshot['modules'][0]['lessons'][0]['content'] = f'```mermaid\n{SOURCE}\n```'
    row.snapshot = snapshot; db_session.commit()
    payload = selection(client, world, version.id)
    response = client.post(BASE+'/pdf-plan', headers=headers(world['owner']), json=payload)
    assert response.status_code == 200, response.text
    key = diagram_key('mermaid', SOURCE)
    assert response.json()['diagrams'] == [{'id': key, 'kind': 'mermaid', 'source': SOURCE}]
    assert client.post(BASE+'/export.pdf', headers=headers(world['owner']), json=payload).status_code == 422
    for svg in ['<svg xmlns="http://www.w3.org/2000/svg"><image href="http://169.254.169.254/latest/meta-data/"/></svg>', '<svg xmlns="http://www.w3.org/2000/svg"><use href="file:///etc/passwd"/></svg>']:
        result = client.post(BASE+'/export.pdf', headers=headers(world['owner']), json={**payload, 'diagrams': [{'id':key, 'svg':svg}]})
        assert result.status_code == 422, result.text
        assert 'application/json' in result.headers['content-type']
    assert db_session.query(AdminEvent).filter_by(action='learning.course_pdf_exported').count() == 0
    result = client.post(BASE+'/export.pdf', headers=headers(world['owner']), json={**payload, 'diagrams': [{'id':key, 'svg':SVG}]})
    assert result.status_code == 200, result.text


def test_pdf_revalidates_owner_after_render_and_does_not_audit_success(client, db_session, world, monkeypatch):
    _, _, _, _, _, version = publish(client, db_session, world['author'])
    payload = selection(client, world, version.id)
    original = course_pdf.build_pdf
    def revoke(*args):
        result = original(*args)
        db_session.execute(update(User).where(User.id == world['owner'].id).values(auth_version=User.auth_version+1).execution_options(synchronize_session=False))
        return result
    monkeypatch.setattr(course_pdf, 'build_pdf', revoke)
    assert client.post(BASE+'/export.pdf', headers=headers(world['owner']), json=payload).status_code == 401
    assert db_session.query(AdminEvent).filter_by(action='learning.course_pdf_exported').count() == 0


@pytest.mark.parametrize('foreign', [True, False])
def test_pdf_rejects_foreign_or_broken_protected_illustration(client, db_session, world, monkeypatch, foreign):
    course, _, _, _, _, version = publish(client, db_session, world['author'])
    foreign_course, *_ = publish(client, db_session, world['foreign'])
    image = CourseImage(course_id=foreign_course.id if foreign else course.id, owner_id=world['author'].id, status='approved', storage_key='image.png', mime_type='image/png')
    db_session.add(image); db_session.flush()
    row = db_session.get(PublishedLearningContent, version.id)
    snapshot = deepcopy(row.snapshot); snapshot['modules'][0]['lessons'][0]['content'] = f'![picture](/images/{image.id}/content)'
    row.snapshot = snapshot; db_session.commit()
    monkeypatch.setattr(image_service, 'storage', lambda: SimpleNamespace(read_bytes=lambda key:b'corrupted PNG'))
    result = client.post(BASE+'/export.pdf', headers=headers(world['owner']), json=selection(client, world, version.id))
    assert result.status_code == (409 if foreign else 422), result.text
    assert db_session.query(AdminEvent).filter_by(action='learning.course_pdf_exported').count() == 0


@pytest.mark.parametrize('source', [
    '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>',
    '<svg><script>alert(1)</script></svg>', '<svg><foreignObject>HTML</foreignObject></svg>',
    '<svg><use href="https://example.test/private"/></svg>', '<svg><path fill="url(https://example.test/p)"/></svg>',
    '<svg onload="alert(1)"/>', '<svg><style>@import "http://example.test/style";</style></svg>',
])
def test_svg_disallows_external_resources_and_active_content(source):
    with pytest.raises(PDFRenderError): safe_svg(source)


def test_safe_local_svg_references_and_overload_fail_closed(client, db_session, world, monkeypatch):
    assert b'url(&quot;#arrow&quot;)' in safe_svg('<svg><path marker-end="url(&quot;#arrow&quot;)"/></svg>')
    assert b'stroke-dasharray="none"' in safe_svg('<svg><path stroke-dasharray="0 0"/></svg>')
    _, _, _, _, _, version = publish(client, db_session, world['author'])
    monkeypatch.setattr(course_pdf, 'PDF_SLOTS', SimpleNamespace(acquire=lambda **_:False))
    response = client.post(BASE+'/export.pdf', headers=headers(world['owner']), json=selection(client, world, version.id))
    assert response.status_code == 503 and response.headers['retry-after'] == '10'


@pytest.mark.parametrize('chunked', [False, True])
def test_pdf_rejects_oversized_json_before_parsing_or_authentication(client, chunked):
    body = b'"' + b'x' * (9 * 1024 * 1024) + b'"'
    response = client.post(BASE+'/export.pdf', content=iter([body]) if chunked else body,
        headers={'Content-Type':'application/json'})
    assert response.status_code == 413
