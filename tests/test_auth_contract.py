from app.services.auth import oauth2_scheme


def test_oauth_password_flow_points_to_auth_login():
    assert oauth2_scheme.model.flows.password.tokenUrl == "/api/auth/login"
