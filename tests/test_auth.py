from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    TokenExpiredError,
    TokenValidationError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def _encode(payload: dict, secret: str | None = None) -> str:
    return jwt.encode(
        payload,
        secret or settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALG,
    )


def _payload(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    return payload


def test_password_hash_round_trip():
    encoded = hash_password("correct-horse-battery-staple")

    assert verify_password("correct-horse-battery-staple", encoded)
    assert not verify_password("wrong-password", encoded)
    assert not verify_password("password", "malformed")


def test_access_token_has_canonical_claims():
    payload = decode_access_token(create_access_token(42))

    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert payload["exp"] > payload["iat"]


def test_expired_access_token_is_rejected():
    now = datetime.now(timezone.utc)
    token = _encode(_payload(iat=now - timedelta(minutes=10), exp=now - timedelta(minutes=1)))

    with pytest.raises(TokenExpiredError):
        decode_access_token(token)


def test_access_token_with_wrong_signature_is_rejected():
    token = _encode(_payload(), secret="different-signing-secret-at-least-32-bytes")

    with pytest.raises(TokenValidationError):
        decode_access_token(token)


def test_legacy_two_part_token_is_rejected():
    with pytest.raises(TokenValidationError):
        decode_access_token("legacy-payload.legacy-signature")


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _payload().items() if key != "sub"},
        _payload(type="refresh"),
    ],
)
def test_access_token_with_invalid_payload_is_rejected(payload):
    with pytest.raises(TokenValidationError):
        decode_access_token(_encode(payload))


def test_register_login_and_me(client):
    credentials = {"email": "  USER@example.com ", "password": "password123"}

    registered = client.post("/api/auth/register", json=credentials)
    assert registered.status_code == 200
    registered_body = registered.json()
    assert registered_body["token_type"] == "bearer"
    assert registered_body["user"]["email"] == "user@example.com"

    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {registered_body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json() == registered_body["user"]

    logged_in = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["user"] == registered_body["user"]


def test_duplicate_registration_is_rejected(client):
    credentials = {"email": "duplicate@example.com", "password": "password123"}

    assert client.post("/api/auth/register", json=credentials).status_code == 200
    duplicate = client.post("/api/auth/register", json=credentials)

    assert duplicate.status_code == 409


def test_login_with_wrong_password_is_rejected(client):
    credentials = {"email": "login@example.com", "password": "password123"}
    assert client.post("/api/auth/register", json=credentials).status_code == 200

    response = client.post(
        "/api/auth/login",
        json={"email": credentials["email"], "password": "incorrect-password"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_requires_access_token(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_unknown_user(client):
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {create_access_token(999999)}"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
