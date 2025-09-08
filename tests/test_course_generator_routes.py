# tests/test_course_generator_routes.py
import json

import pytest

from tests.factories import make_course, make_cs


@pytest.fixture
def patch_llm_modules(monkeypatch):
    def ctor(_model):
        class C:
            def generate(self, prompt, max_tokens=None):
                return json.dumps(
                    {
                        "modules": [
                            {"title": "Module A", "lessons": [{"title": "L1", "description": ""}]}
                        ]
                    }
                )

        return C(), "dummy"

    from app.services import generation_service as gs

    monkeypatch.setitem(gs.SUPPORTED_ENGINES, "gigachat", ctor)
    return ctor


def test_generate_modules_ok(client, db_session, patch_llm_modules):
    c = make_course(db_session)
    cs = make_cs(db_session, course_id=c.id)
    r = client.get(f"/api/courses/{c.id}/generate_modules", params={"cs_id": cs.id})
    assert r.status_code == 200
    # модули реально записались
    mods = client.get(f"/api/courses/{c.id}/modules/").json()
    assert len(mods) == 1
    assert mods[0]["title"] == "Module A"


def test_generate_modules_bad_cs(client, db_session):
    c = make_course(db_session)
    r = client.get(f"/api/courses/{c.id}/generate_modules", params={"cs_id": 999})
    assert r.status_code == 404
