"""Admin template CRUD + the read-at-generation freshness guarantee.

Freshness itself was already architectural (generation.py reads
template.instructions fresh from the DB every call, no cache). This file
adds the test the spec explicitly asks for: edit a template, generate
again on the same encounter with no refresh, and see the new instructions
take effect immediately.
"""

from sqlalchemy import select

from app import llm
from app.models import AuditLog
from tests.conftest import login
from tests.test_generation import make_fake_stream


def test_public_list_excludes_instructions_and_inactive(client, users, db):
    login(client, "admin@test.example")
    active = client.post(
        "/api/admin/templates",
        json={"name": "Active One", "description": "d", "instructions": "secret prompt text"},
    ).json()
    inactive = client.post(
        "/api/admin/templates",
        json={"name": "Inactive One", "description": "d", "instructions": "secret prompt text"},
    ).json()
    client.patch(f"/api/admin/templates/{inactive['id']}", json={"is_active": False})

    public = client.get("/api/templates").json()
    names = {t["name"] for t in public}
    assert "Active One" in names
    assert "Inactive One" not in names
    assert all("instructions" not in t for t in public)


def test_admin_list_includes_instructions_and_inactive(client, users):
    login(client, "admin@test.example")
    created = client.post(
        "/api/admin/templates",
        json={"name": "T", "description": "d", "instructions": "prompt body"},
    ).json()
    client.patch(f"/api/admin/templates/{created['id']}", json={"is_active": False})

    admin_list = client.get("/api/admin/templates").json()
    row = next(t for t in admin_list if t["id"] == created["id"])
    assert row["instructions"] == "prompt body"
    assert row["is_active"] is False


def test_partial_update_only_touches_sent_fields(client, users):
    login(client, "admin@test.example")
    created = client.post(
        "/api/admin/templates",
        json={"name": "Original", "description": "orig desc", "instructions": "orig instr"},
    ).json()

    client.patch(f"/api/admin/templates/{created['id']}", json={"description": "new desc"})

    admin_list = client.get("/api/admin/templates").json()
    row = next(t for t in admin_list if t["id"] == created["id"])
    assert row["name"] == "Original"
    assert row["instructions"] == "orig instr"
    assert row["description"] == "new desc"


def test_template_mutations_are_audited(client, users, db):
    login(client, "admin@test.example")
    created = client.post(
        "/api/admin/templates",
        json={"name": "T", "description": "", "instructions": "x"},
    ).json()
    client.patch(f"/api/admin/templates/{created['id']}", json={"description": "y"})

    actions = db.scalars(
        select(AuditLog.action)
        .where(AuditLog.entity_id == created["id"], AuditLog.entity_type == "template")
        .order_by(AuditLog.id)
    ).all()
    assert actions == ["template_create", "template_update"]


def test_provider_forbidden_from_template_admin_routes(client, users):
    login(client, "a@test.example")
    assert client.get("/api/admin/templates").status_code == 403
    assert client.post(
        "/api/admin/templates",
        json={"name": "x", "instructions": "y"},
    ).status_code == 403


def test_update_nonexistent_template_404s(client, users):
    login(client, "admin@test.example")
    response = client.patch("/api/admin/templates/99999", json={"description": "x"})
    assert response.status_code == 404


# ---- template freshness: read-at-generation, no refresh (spec-required) ------


def test_updated_template_takes_effect_on_next_generation_no_refresh(
    client, users, db, monkeypatch
):
    login(client, "admin@test.example")
    template = client.post(
        "/api/admin/templates",
        json={"name": "T", "description": "", "instructions": "STYLE_VERSION_ONE"},
    ).json()

    login(client, "a@test.example")
    encounter = client.post(
        "/api/encounters",
        json={
            "first_name": "Pat", "last_name": "Ient", "dob": "1980-01-01",
            "template_id": template["id"],
        },
    ).json()
    client.patch(
        f"/api/encounters/{encounter['encounter_id']}",
        json={"transcript": "sore throat for two days"},
    )

    fake = make_fake_stream()
    monkeypatch.setattr(llm, "stream_completion", fake)

    # First generation: prompt carries the original instructions.
    client.get(f"/api/encounters/{encounter['encounter_id']}/generate")
    assert "STYLE_VERSION_ONE" in fake.calls[0]["user_prompt"]

    # Admin edits the template — no cache to bust, nothing to refresh.
    login(client, "admin@test.example")
    client.patch(
        f"/api/admin/templates/{template['id']}",
        json={"instructions": "STYLE_VERSION_TWO"},
    )

    # Same encounter, same session, second generate: new instructions apply
    # immediately, with zero client-side refresh action.
    login(client, "a@test.example")
    client.get(f"/api/encounters/{encounter['encounter_id']}/generate")
    assert "STYLE_VERSION_TWO" in fake.calls[1]["user_prompt"]
    assert "STYLE_VERSION_ONE" not in fake.calls[1]["user_prompt"]
