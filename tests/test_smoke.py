import os
from fastapi.testclient import TestClient
import importlib

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

app_mod = importlib.import_module("main")
client = TestClient(app_mod.app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200


def test_healthz():
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_readiness(client):
    r = client.get("/api/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("db") == "up"
    
