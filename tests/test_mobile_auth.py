from app.models.integrations import MobileSession, MobilePushDevice
import pytest


def bearer(result):
    return {"Authorization": "Bearer " + result["access_token"]}


def test_mobile_login_rotates_refresh_and_logout_revokes_access(client, auth_user, db_session):
    logged = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123", "device_name": "Pixel"})
    assert logged.status_code == 200, logged.text
    tokens = logged.json()
    row = db_session.query(MobileSession).one()
    assert len(row.refresh_token_hash) == 64 and tokens["refresh_token"] not in row.refresh_token_hash
    assert client.get("/api/auth/me", headers=bearer(tokens)).status_code == 200

    refreshed = client.post("/api/auth/mobile/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert client.get("/api/auth/me", headers=bearer(tokens)).status_code == 401
    assert client.post("/api/auth/mobile/logout", headers=bearer(rotated)).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(rotated)).status_code == 401

    other = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123"}).json()
    other_rotated = client.post("/api/auth/mobile/refresh", json={"refresh_token": other["refresh_token"]}).json()
    assert client.post("/api/auth/mobile/refresh", json={"refresh_token": other["refresh_token"]}).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(other_rotated)).status_code == 401


def test_mobile_logout_all_revokes_other_devices(client, auth_user):
    first = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123"}).json()
    second = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123", "device_name": "Tablet"}).json()
    assert client.post("/api/auth/mobile/logout-all", headers=bearer(first)).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(second)).status_code == 401


def test_mobile_bootstrap_reports_fixed_origin_and_compatibility(client):
    result = client.get("/api/auth/mobile/bootstrap")
    assert result.status_code == 200
    body = result.json()
    assert body["api_origin"] and body["display_domain"]
    assert body["api_compatibility"] and body["minimum_app_version"]
    assert body["recommended_app_version"] is None
    assert body["app_store_url_ios"] is None and body["app_store_url_android"] is None


def test_mobile_bootstrap_reports_deployment_overrides(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MOBILE_APP_MIN_VERSION", "2.3.0")
    monkeypatch.setattr(settings, "MOBILE_APP_RECOMMENDED_VERSION", "2.4.1")
    monkeypatch.setattr(settings, "MOBILE_APP_STORE_URL_IOS", "https://apps.apple.com/app/id123")
    monkeypatch.setattr(settings, "MOBILE_APP_STORE_URL_ANDROID", "https://play.google.com/store/apps/details?id=app")
    monkeypatch.setattr(settings, "MOBILE_API_COMPATIBILITY", "2")
    body = client.get("/api/auth/mobile/bootstrap").json()
    assert body["minimum_app_version"] == "2.3.0"
    assert body["recommended_app_version"] == "2.4.1"
    assert body["app_store_url_ios"].startswith("https://apps.apple.com/")
    assert body["app_store_url_android"].startswith("https://play.google.com/")
    assert body["api_compatibility"] == "2"


@pytest.mark.parametrize("overrides", [
    {"MOBILE_APP_MIN_VERSION": "1.2"},
    {"MOBILE_APP_MIN_VERSION": "v1.2.3"},
    {"MOBILE_APP_MIN_VERSION": "2.0.0", "MOBILE_APP_RECOMMENDED_VERSION": "1.9.9"},
    {"MOBILE_APP_STORE_URL_IOS": "http://apps.apple.com/app/id123"},
])
def test_mobile_release_settings_reject_invalid_values(overrides):
    from app.core.config import Settings
    with pytest.raises(Exception):
        Settings(_env_file=None, DATABASE_URL="sqlite://", JWT_SECRET="x" * 40, **overrides)


def test_mobile_policy_defaults_are_restrictive_and_push_is_bound_to_session(client, auth_user, db_session, monkeypatch):
    from pydantic import SecretStr
    from cryptography.fernet import Fernet
    from app.core.integration_config import integration_settings
    monkeypatch.setattr(integration_settings, "DATA_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode()))
    tokens = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123"}).json()
    policy = client.get("/api/auth/mobile/policy", headers=bearer(tokens))
    assert policy.status_code == 200
    assert policy.json() == {"material_downloads": False, "offline_access": False,
                             "cache_ttl_hours": 24, "backups": False, "features": {}}
    registered = client.put("/api/auth/mobile/push-device", headers=bearer(tokens), json={
        "token": "provider-secret-push-token", "platform": "ios"})
    assert registered.status_code == 200, registered.text
    row = db_session.query(MobilePushDevice).one()
    assert row.session_id == db_session.query(MobileSession).one().id
    assert row.token_hash != "provider-secret-push-token"
    assert row.token_encrypted != "provider-secret-push-token"
    assert "provider-secret-push-token" not in registered.text
    assert client.post("/api/auth/mobile/logout", headers=bearer(tokens)).status_code == 200
    assert db_session.query(MobilePushDevice).count() == 0


def test_mobile_push_preferences_and_token_transfer(client, auth_user, db_session, monkeypatch):
    from pydantic import SecretStr
    from cryptography.fernet import Fernet
    from app.core.integration_config import integration_settings
    from app.services.integration_secrets import unseal
    monkeypatch.setattr(integration_settings, "DATA_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode()))
    first = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123"}).json()
    second = client.post("/api/auth/mobile/login", json={"email": auth_user.email, "password": "password123"}).json()
    assert client.get("/api/auth/mobile/push-preferences", headers=bearer(first)).json() == {
        "assignment_enabled": True, "deadline_enabled": True, "result_enabled": True, "timezone": "UTC"}
    saved = client.put("/api/auth/mobile/push-preferences", headers=bearer(first), json={
        "assignment_enabled": False, "deadline_enabled": True, "result_enabled": False, "timezone": "Europe/Moscow"})
    assert saved.status_code == 200 and saved.json()["timezone"] == "Europe/Moscow"
    assert client.put("/api/auth/mobile/push-preferences", headers=bearer(first), json={
        "assignment_enabled": True, "deadline_enabled": True, "result_enabled": True, "timezone": "Not/AZone"}).status_code == 422
    assert client.put("/api/auth/mobile/push-device", headers=bearer(first), json={
        "token": "same-installation-token", "platform": "android"}).status_code == 200
    first_session_id = db_session.query(MobilePushDevice).one().session_id
    assert client.put("/api/auth/mobile/push-device", headers=bearer(second), json={
        "token": "same-installation-token", "platform": "android"}).status_code == 200
    device = db_session.query(MobilePushDevice).one()
    assert device.session_id != first_session_id
    assert unseal(device.token_encrypted) == "same-installation-token"
    assert client.post("/api/auth/mobile/logout-all", headers=bearer(second)).status_code == 200
    assert db_session.query(MobilePushDevice).count() == 0
