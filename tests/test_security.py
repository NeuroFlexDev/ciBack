from datetime import datetime, timezone

from jose import jwt

from app.services import security


class DummySettings:
    JWT_SECRET = "test-secret"
    JWT_ALG = "HS512"
    ACCESS_TOKEN_TTL_MINUTES = 5


def test_create_access_token_uses_runtime_settings(monkeypatch):
    monkeypatch.setattr(security, "settings", DummySettings())

    token = security.create_access_token({"sub": "42"})
    payload = jwt.decode(token, DummySettings.JWT_SECRET, algorithms=[DummySettings.JWT_ALG])

    assert payload["sub"] == "42"
    assert payload["exp"] > datetime.now(timezone.utc).timestamp()
