"""Admin audit log view."""

from tests.conftest import login


def test_audit_log_requires_admin(client, users):
    login(client, "a@test.example")
    assert client.get("/api/admin/audit").status_code == 403


def test_audit_log_shows_admin_actions_newest_first(client, users):
    login(client, "admin@test.example")
    t1 = client.post(
        "/api/admin/templates",
        json={"name": "First", "description": "", "instructions": "x"},
    ).json()
    client.patch(f"/api/admin/providers/{users['provider_a'].id}", json={"is_active": False})

    entries = client.get("/api/admin/audit").json()
    actions = [e["action"] for e in entries]
    # Most recent action (provider_deactivate) appears before the earlier one.
    assert actions.index("provider_deactivate") < actions.index("template_create")

    template_entry = next(e for e in entries if e["action"] == "template_create")
    assert template_entry["entity_id"] == t1["id"]
    assert template_entry["entity_type"] == "template"
    assert template_entry["user_name"] == "Admin"


def test_audit_log_limit_is_bounded(client, users):
    login(client, "admin@test.example")
    assert client.get("/api/admin/audit", params={"limit": 500}).status_code == 422
    assert client.get("/api/admin/audit", params={"limit": 0}).status_code == 422
    assert client.get("/api/admin/audit", params={"limit": 1}).status_code == 200
