"""Core scribe workflow: returning-patient match, autosave persistence,
append-only save."""

from tests.conftest import login


def _create(client, **overrides):
    payload = {
        "first_name": "Margaret",
        "last_name": "Thompson",
        "dob": "1954-03-17",
        **overrides,
    }
    response = client.post("/api/encounters", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_new_patient_then_returning_match(client, users):
    login(client, "a@test.example")

    first = _create(client)
    assert first["returning"] is False
    assert first["prior_encounters"] == 0

    # Same identity, different casing — must match, not duplicate.
    second = _create(client, first_name="margaret", last_name="THOMPSON")
    assert second["returning"] is True
    assert second["prior_encounters"] == 1
    assert second["patient"]["id"] == first["patient"]["id"]


def test_autosave_persists_transcript_and_draft(client, users):
    login(client, "a@test.example")
    enc_id = _create(client)["encounter_id"]

    draft = {
        "subjective": "knee pain",
        "objective": "",
        "assessment": "",
        "plan": "PT referral",
        "icd_codes": [{"code": "M25.561", "description": "Pain in right knee"}],
    }
    patch = client.patch(
        f"/api/encounters/{enc_id}",
        json={"transcript": "patient reports knee pain", "draft_note": draft},
    )
    assert patch.status_code == 200

    # "Refresh the page": a fresh GET must return exactly what was autosaved.
    detail = client.get(f"/api/encounters/{enc_id}").json()
    assert detail["transcript"] == "patient reports knee pain"
    assert detail["draft_note"]["subjective"] == "knee pain"
    assert detail["draft_note"]["icd_codes"][0]["code"] == "M25.561"
    assert detail["status"] == "draft"


def test_transcript_only_patch_preserves_draft_note(client, users):
    login(client, "a@test.example")
    enc_id = _create(client)["encounter_id"]

    client.patch(f"/api/encounters/{enc_id}", json={"draft_note": {"plan": "rest"}})
    client.patch(f"/api/encounters/{enc_id}", json={"transcript": "updated"})

    detail = client.get(f"/api/encounters/{enc_id}").json()
    assert detail["transcript"] == "updated"
    assert detail["draft_note"]["plan"] == "rest"  # not wiped by the 2nd patch


def test_save_appends_versions_and_clears_draft(client, users):
    login(client, "a@test.example")
    enc_id = _create(client)["encounter_id"]
    client.patch(f"/api/encounters/{enc_id}", json={"draft_note": {"plan": "v1 plan"}})

    v1 = client.post(
        f"/api/encounters/{enc_id}/save",
        json={"subjective": "s1", "plan": "p1",
              "icd_codes": [{"code": "I10", "description": "HTN"}]},
    )
    assert v1.status_code == 200
    assert v1.json()["version_number"] == 1

    v2 = client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "s2"})
    assert v2.json()["version_number"] == 2

    detail = client.get(f"/api/encounters/{enc_id}").json()
    assert detail["status"] == "saved"
    assert detail["draft_note"] is None  # scratch cleared on save
    assert detail["latest_version"]["version_number"] == 2
    assert detail["latest_version"]["subjective"] == "s2"


def test_provider_b_cannot_touch_a_encounter(client, users):
    login(client, "a@test.example")
    enc_id = _create(client)["encounter_id"]

    login(client, "b@test.example")
    assert client.patch(f"/api/encounters/{enc_id}", json={"transcript": "x"}).status_code == 404
    assert client.post(f"/api/encounters/{enc_id}/save", json={}).status_code == 404
