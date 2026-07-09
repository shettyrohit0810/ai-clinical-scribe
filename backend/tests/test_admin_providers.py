"""Admin provider management: create, deactivate/reactivate, audit trail."""

from sqlalchemy import select

from app.models import AuditLog
from tests.conftest import TEST_PASSWORD, login


def test_admin_can_create_provider_and_it_can_log_in(client, users, db):
    login(client, "admin@test.example")
    response = client.post(
        "/api/admin/providers",
        json={
            "email": "New.Provider@Clinic.example",
            "full_name": "Dr. New Provider",
            "password": "a-strong-password",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_active"] is True
    assert body["role"] == "provider"
    # Email normalized to match the login() lookup exactly.
    assert body["email"] == "new.provider@clinic.example"

    login_response = client.post(
        "/api/auth/login",
        json={"email": "New.Provider@Clinic.example", "password": "a-strong-password"},
    )
    assert login_response.status_code == 200

    audit_row = db.scalar(select(AuditLog).where(AuditLog.action == "provider_create"))
    assert audit_row.entity_id == body["id"]


def test_duplicate_email_returns_400(client, users):
    login(client, "admin@test.example")
    payload = {
        "email": "dupe@clinic.example", "full_name": "First",
        "password": "a-strong-password",
    }
    assert client.post("/api/admin/providers", json=payload).status_code == 201
    payload["full_name"] = "Second"
    response = client.post("/api/admin/providers", json=payload)
    assert response.status_code == 400


def test_deactivate_then_reactivate_provider(client, users, db):
    login(client, "admin@test.example")
    provider_id = users["provider_a"].id

    deactivate = client.patch(
        f"/api/admin/providers/{provider_id}", json={"is_active": False}
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False

    # Deactivation is immediate — the very next request from that provider
    # is rejected, per the existing get_current_user behavior.
    login(client, "admin@test.example")  # re-establish admin cookie
    denied = client.post(
        "/api/auth/login",
        json={"email": "a@test.example", "password": TEST_PASSWORD},
    )
    assert denied.status_code == 403

    reactivate = client.patch(
        f"/api/admin/providers/{provider_id}", json={"is_active": True}
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["is_active"] is True

    actions = db.scalars(
        select(AuditLog.action)
        .where(AuditLog.entity_id == provider_id, AuditLog.entity_type == "user")
        .order_by(AuditLog.id)
    ).all()
    assert actions == ["provider_deactivate", "provider_activate"]


def test_provider_status_route_cannot_target_an_admin_account(client, users):
    # The route is scoped to role=provider; an admin id (role=admin) 404s —
    # which also rules out an admin locking themselves out through this
    # endpoint, since their own account can never match here.
    login(client, "admin@test.example")
    response = client.patch(
        f"/api/admin/providers/{users['admin'].id}", json={"is_active": False}
    )
    assert response.status_code == 404


def test_provider_forbidden_from_admin_routes(client, users):
    login(client, "a@test.example")
    assert client.get("/api/admin/providers").status_code == 403
    assert client.post(
        "/api/admin/providers",
        json={"email": "x@y.com", "full_name": "X", "password": "a-strong-password"},
    ).status_code == 403
