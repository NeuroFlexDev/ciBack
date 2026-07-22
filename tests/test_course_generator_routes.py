# tests/test_course_generator_routes.py
import json

import pytest

from tests.factories import make_course, make_cs


@pytest.fixture
def patch_llm_modules(monkeypatch):
    def ctor():
        class C:
            def generate(self, prompt, max_tokens=None):
                return json.dumps(
                    {
                        "modules": [
                            {"title": "Module A", "lessons": [{"title": "L1", "description": ""}]}
                        ]
                    }
                )

        return C()

    from app.services import generation_service as gs

    monkeypatch.setitem(gs.SUPPORTED_ENGINES, "huggingface", ctor)
    monkeypatch.setattr(gs, "get_cached_external_context", lambda *args, **kwargs: "")
    return ctor


def test_generate_modules_ok(client, db_session, patch_llm_modules, auth_user, auth_headers):
    c = make_course(db_session, owner_id=auth_user.id)
    cs = make_cs(db_session, course_id=c.id, owner_id=auth_user.id)
    r = client.get(f"/api/courses/{c.id}/generate_modules", params={"cs_id": cs.id}, headers=auth_headers)
    assert r.status_code == 200
    # модули реально записались
    mods = client.get(f"/api/courses/{c.id}/modules/", headers=auth_headers).json()

    print("modules endpoint response:", mods)
    assert isinstance(mods, list), f"API did not return a list: {mods}"
    assert len(mods) == 1, f"modules returned: {mods}"

    assert len(mods) == 1
    assert mods[0]["title"] == "Module A"


def test_generate_modules_bad_cs(client, db_session, auth_user, auth_headers):
    c = make_course(db_session, owner_id=auth_user.id)
    r = client.get(f"/api/courses/{c.id}/generate_modules", params={"cs_id": 999}, headers=auth_headers)
    assert r.status_code == 404
