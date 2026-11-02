from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('profile', ['dev-platform/frontend-nginx.conf', 'customer/frontend.conf'])
def test_bound_frontend_profiles_serve_personal_pages_on_refresh(profile):
    config = (ROOT / 'deploy' / profile).read_text()
    pattern = re.search(r'location ~ (\^/\([^\n]+?) \{', config)
    assert pattern
    routes = re.compile(pattern[1])
    for path in ('/results', '/notifications', '/help', '/learning/123', '/courses/123/canvas'):
        assert routes.fullmatch(path), f'{profile}: {path} would return 404 on refresh'
    assert not routes.fullmatch('/unknown-page')
    assert not routes.fullmatch('/assets/missing.js')


def test_personal_pages_are_private_in_shared_caddy_profile():
    config = (ROOT / 'deploy/dev-platform/Caddyfile').read_text()
    match = re.search(r'@privatePages path ([^\n]+)', config)
    assert match
    paths = match[1].split()
    assert {'/results', '/notifications', '/help'} <= set(paths)
