from app.models.user import User


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


def register_and_login(client, db_session, *, email: str, password: str, full_name: str, role: str = "student"):
    register_response = register_user(client, email, password, full_name)
    assert register_response.status_code == 201

    user = db_session.query(User).filter(User.email == email).one()
    user.role = role
    db_session.commit()
    db_session.refresh(user)

    login_response = login_user(client, email, password)
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return user, token
