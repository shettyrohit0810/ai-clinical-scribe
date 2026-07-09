"""Auth matrix: login success/failure, expiry, deactivation."""

from app.auth import ACCESS_TOKEN_COOKIE, create_access_token
from tests.conftest import TEST_PASSWORD, login


def test_login_success_sets_cookie_and_me_works(client, users):
    response = client.post(
        "/api/auth/login",
        json={"email": "a@test.example", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    assert ACCESS_TOKEN_COOKIE in response.cookies
    assert response.json()["email"] == "a@test.example"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "provider"


def test_wrong_password_and_unknown_email_are_indistinguishable(client, users):
    wrong_pw = client.post(
        "/api/auth/login", json={"email": "a@test.example", "password": "nope"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "ghost@test.example", "password": "nope"}
    )
    # Same status AND same body: no account enumeration.
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_no_cookie_gives_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_expired_token_gives_401_session_expired(client, users, db):
    user = users["provider_a"]
    expired = create_access_token(user, expires_minutes=-1)
    client.cookies.set(ACCESS_TOKEN_COOKIE, expired)
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    # Distinct detail drives the Phase 9 re-login-and-retry modal.
    assert response.json()["detail"] == "Session expired"


def test_deactivated_provider_gets_403_on_next_request(client, users, db):
    login(client, "a@test.example")
    assert client.get("/api/auth/me").status_code == 200

    # Admin flips the flag; the still-valid JWT must stop working immediately.
    users["provider_a"].is_active = False
    db.commit()

    response = client.get("/api/auth/me")
    assert response.status_code == 403
    assert response.json()["detail"] == "Account deactivated"


def test_deactivated_user_cannot_log_in(client, users, db):
    users["provider_a"].is_active = False
    db.commit()
    response = client.post(
        "/api/auth/login",
        json={"email": "a@test.example", "password": TEST_PASSWORD},
    )
    assert response.status_code == 403
