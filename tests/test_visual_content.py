import base64
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.config import AIProvider, settings
from app.models.ai_resources import AIResourceUsage
from app.models.materials import CourseImage
from app.services import image_provider, image_service
from app.services.diagram_policy import validate_generated_diagrams
from tests.factories import make_course
from tests.test_materials_release import png


def test_generated_diagram_roundtrip_and_schema_validation():
    from app.schemas.agentic_pipeline import LessonSection
    source = 'Explanation\n\n```mermaid\nflowchart LR\n  A["Начало"] --> B["Проверка"]\n```\n\nConclusion'
    assert validate_generated_diagrams(source) == source
    assert LessonSection(id='section:test', heading='Test', content_markdown=source, source_ref_ids=['src:doc:1']).content_markdown == source
    with pytest.raises(ValueError):
        LessonSection(id='section:test', heading='Test', content_markdown='```mermaid\nflowchart LR\nclick A "https://evil.invalid"\n```', source_ref_ids=['src:doc:1'])


@pytest.mark.parametrize('source', ['%%{init: {securityLevel: loose}}%%\nflowchart LR\nA-->B',
    'flowchart LR\nA["<img src=x onerror=alert(1)>"]', 'flowchart LR\nA@{img:"https://evil.invalid/a"}',
    'flowchart LR\nclassDef x fill:url(https://evil.invalid/x)', '@startuml\n!include https://evil.invalid\n@enduml',
    'flowchart LR\n' + '\n'.join('A-->B' for _ in range(161))])
def test_generation_rejects_external_or_unbounded_diagrams(source):
    with pytest.raises(ValueError):
        validate_generated_diagrams(f'```mermaid\n{source}\n```')


@pytest.fixture
def configured_images(monkeypatch, tmp_path, db_session):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(settings, 'AI_PROVIDERS', [AIProvider(id='images', base_url='https://provider.invalid/v1')])
    monkeypatch.setattr(settings, 'IMAGE_PROVIDER', 'images')
    monkeypatch.setattr(settings, 'IMAGE_MODEL', 'openai/gpt-image-2')
    monkeypatch.setattr(settings, 'IMAGE_PRICE_RUB', Decimal('20'))
    monkeypatch.setattr(settings, 'IMAGE_API_MODE', 'auto')
    monkeypatch.setattr(settings, 'IMAGE_QUALITY', 'low')
    monkeypatch.setattr('app.ai.providers.available', lambda _: True)
    monkeypatch.setattr('app.ai.providers.credential', lambda _: 'test-never-network')
    monkeypatch.setattr(image_service, 'enqueue_image', lambda _: None)
    monkeypatch.setattr('app.database.db.SessionLocal', lambda: db_session)
    monkeypatch.setattr(db_session, 'close', lambda: None)


def test_real_image_bytes_idempotency_approval_and_frozen_options(client, db_session, auth_user, auth_headers, configured_images, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    path = f'/api/courses/{course.id}/images/generate'
    payload = {'prompt': 'Educational illustration', 'request_id': 'real-image-test-001'}
    first = client.post(path, json=payload, headers=auth_headers)
    assert first.status_code == 202, first.text
    item_id = first.json()['id']
    monkeypatch.setattr(settings, 'IMAGE_QUALITY', 'high')
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png()).decode())])
    monkeypatch.setattr('openai.OpenAI', lambda **kwargs: SimpleNamespace(images=SimpleNamespace(generate=generate)))
    image_service.execute_image_generation(item_id)
    image_service.execute_image_generation(item_id)
    assert client.post(path, json=payload, headers=auth_headers).json()['id'] == item_id
    assert len(calls) == 1 and calls[0]['quality'] == 'low' and 'response_format' not in calls[0]
    row = db_session.get(CourseImage, item_id)
    assert (row.status, row.width, row.height) == ('pending_review', 20, 10)
    content = client.get(f'/api/images/{item_id}/content', headers=auth_headers).content
    with Image.open(BytesIO(content)) as image:
        assert image.size == (20, 10) and image.convert('RGB').getpixel((0, 0)) == (0, 0, 255)
    assert db_session.query(AIResourceUsage).filter_by(key=f'image:{item_id}').one().cost_rub == Decimal('20')
    assert client.post(f'/api/images/{item_id}/review', json={'decision': 'approved'}, headers=auth_headers).status_code == 200


def test_timeout_has_one_submission_and_keeps_charge_receipt(client, db_session, auth_user, auth_headers, configured_images, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    item_id = client.post(f'/api/courses/{course.id}/images/generate', json={'prompt': 'Image timeout', 'request_id': 'timeout-request-01'}, headers=auth_headers).json()['id']
    calls = []
    def generate(**kwargs):
        calls.append(True)
        raise TimeoutError('uncertain provider outcome')
    monkeypatch.setattr('openai.OpenAI', lambda **kwargs: SimpleNamespace(images=SimpleNamespace(generate=generate)))
    image_service.execute_image_generation(item_id); image_service.execute_image_generation(item_id)
    receipt = db_session.query(AIResourceUsage).filter_by(key=f'image:{item_id}').one()
    assert calls == [True] and receipt.status == 'unknown' and receipt.cost_rub == Decimal('20')


def test_legacy_config_fails_unsent_and_queue_failure_releases_reservation(client, db_session, auth_user, auth_headers, configured_images, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    path = f'/api/courses/{course.id}/images/generate'
    item_id = client.post(path, json={'prompt': 'Legacy image', 'request_id': 'legacy-request-01'}, headers=auth_headers).json()['id']
    row = db_session.get(CourseImage, item_id); row.generation_config = None; db_session.commit()
    image_service.execute_image_generation(item_id)
    assert row.error_code == 'image_configuration_changed'
    assert db_session.query(AIResourceUsage).filter_by(key=f'image:{item_id}').one().cost_rub == 0
    def queue_failure(_): raise ConnectionError('queue offline')
    monkeypatch.setattr(image_service, 'enqueue_image', queue_failure)
    response = client.post(path, json={'prompt': 'Queued image', 'request_id': 'queue-request-02'}, headers=auth_headers)
    receipt = db_session.query(AIResourceUsage).filter_by(key=f'image:{response.json()["id"]}').one()
    assert response.json()['error_code'] == 'image_queue_unavailable' and receipt.status == 'unavailable' and receipt.cost_rub == 0


def test_missing_price_disables_capability(client, auth_headers, configured_images, monkeypatch):
    monkeypatch.setattr(settings, 'IMAGE_PRICE_RUB', None)
    result = client.get('/api/materials/capabilities', headers=auth_headers).json()
    assert result['image_generation_available'] is False and result['image_generation_error'] == 'image_price_unconfigured'


def test_lost_enqueue_ack_never_refunds_a_claimed_image(client, db_session, auth_user, auth_headers, configured_images, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    def claimed_then_error(image_id):
        from app.services.ai_resources import claim
        assert claim(db_session, f'image:{image_id}')
        raise ConnectionError('acknowledgement lost after worker claim')
    monkeypatch.setattr(image_service, 'enqueue_image', claimed_then_error)
    response = client.post(f'/api/courses/{course.id}/images/generate', json={'prompt': 'Claimed image', 'request_id': 'claimed-image-01'}, headers=auth_headers)
    receipt = db_session.query(AIResourceUsage).filter_by(key=f'image:{response.json()["id"]}').one()
    assert receipt.status == 'running' and receipt.cost_rub == Decimal('20')
    assert response.json()['status'] == 'queued'


def test_webp_is_normalized_without_metadata():
    output = BytesIO(); Image.new('RGBA', (12, 10), 'red').save(output, 'WEBP', exif=b'private')
    content, mime, width, height = image_service.normalize_image(output.getvalue())
    assert mime == 'image/png' and (width, height) == (12, 10) and b'private' not in content


@pytest.mark.parametrize('url', ['http://cdn.example/image', 'https://cdn.example@127.0.0.1/image',
    'https://cdn.example:444/image', 'https://unapproved.example/image', 'https://cdn.example/image#fragment',
    'file:///private', 'https://cdn.example/\r\nHost: localhost'])
def test_url_requires_exact_https_host_before_dns(monkeypatch, url):
    monkeypatch.setattr(settings, 'IMAGE_DOWNLOAD_HOSTS', ['cdn.example'])
    monkeypatch.setattr(image_provider, 'public_addresses', lambda _: pytest.fail('unexpected DNS'))
    with pytest.raises(image_provider.ImageResultError): image_provider.download_image(url, timeout=10)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1', '::ffff:127.0.0.1'])
def test_url_dns_rejects_private_and_metadata_addresses(monkeypatch, address):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(None, None, None, None, (address, 443))])
    with pytest.raises(image_provider.ImageResultError): image_provider.public_addresses('cdn.example')


def test_download_pins_public_ip_has_no_auth_redirect_or_unbounded_read(monkeypatch):
    monkeypatch.setattr(settings, 'IMAGE_DOWNLOAD_HOSTS', ['cdn.example'])
    monkeypatch.setattr(image_provider, 'public_addresses', lambda _: ['8.8.8.8'])
    seen = []
    class Connection:
        def __init__(self, *args): seen.append(args)
        def request(self, method, path, headers): seen.append((method, path, headers))
        def getresponse(self): return SimpleNamespace(status=200, getheader=lambda name, default=None: default, read=lambda limit: (seen.append(limit) or png()))
        def close(self): seen.append('closed')
    monkeypatch.setattr(image_provider, 'PinnedHTTPSConnection', Connection)
    assert image_provider.download_image('https://cdn.example/image?signature=opaque', timeout=30) == png()
    assert seen[0] == ('cdn.example', '8.8.8.8', 30) and 'Authorization' not in seen[1][2]
    assert seen[2] == settings.IMAGE_MAX_BYTES + 1 and seen[-1] == 'closed'
    monkeypatch.setattr(Connection, 'getresponse', lambda _: SimpleNamespace(status=302))
    with pytest.raises(image_provider.ImageResultError): image_provider.download_image('https://cdn.example/redirect', timeout=30)
