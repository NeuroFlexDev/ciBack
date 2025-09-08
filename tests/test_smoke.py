from fastapi.testclient import TestClient
import importlib

app_mod = importlib.import_module("main")
client = TestClient(app_mod.app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200


def test_healthz():
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json().get("ok") is True
