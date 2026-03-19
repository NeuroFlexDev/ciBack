def register_user(client, email: str, password: str, full_name: str):
    return client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
        },
    )


def login_user(client, email: str, password: str):
    return client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )


def auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_auth_register_login_and_read_me(client):
    register = register_user(client, "auth1@example.com", "secret123", "Auth One")
    assert register.status_code == 201

    login = login_user(client, "auth1@example.com", "secret123")
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get("/api/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["email"] == "auth1@example.com"
    assert me.json()["full_name"] == "Auth One"


def test_auth_update_me_rejects_duplicate_email(client):
    register_user(client, "auth2@example.com", "secret123", "Auth Two")
    register_user(client, "auth3@example.com", "secret123", "Auth Three")

    login = login_user(client, "auth2@example.com", "secret123")
    token = login.json()["access_token"]

    updated = client.patch(
        "/api/auth/me",
        headers=auth_headers(token),
        json={"full_name": "Updated Name"},
    )
    assert updated.status_code == 200
    assert updated.json()["full_name"] == "Updated Name"

    duplicate = client.patch(
        "/api/auth/me",
        headers=auth_headers(token),
        json={"email": "auth3@example.com"},
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Email already registered"


def test_auth_change_password_requires_old_password_and_accepts_new_password(client):
    register_user(client, "auth4@example.com", "secret123", "Auth Four")

    login = login_user(client, "auth4@example.com", "secret123")
    token = login.json()["access_token"]

    wrong_old_password = client.post(
        "/api/auth/change-password",
        headers=auth_headers(token),
        json={"old_password": "bad-secret", "new_password": "newsecret123"},
    )
    assert wrong_old_password.status_code == 400
    assert wrong_old_password.json()["detail"] == "Wrong old password"

    changed = client.post(
        "/api/auth/change-password",
        headers=auth_headers(token),
        json={"old_password": "secret123", "new_password": "newsecret123"},
    )
    assert changed.status_code == 200
    assert changed.json() == {"ok": True}

    old_login = login_user(client, "auth4@example.com", "secret123")
    assert old_login.status_code == 401

    new_login = login_user(client, "auth4@example.com", "newsecret123")
    assert new_login.status_code == 200
