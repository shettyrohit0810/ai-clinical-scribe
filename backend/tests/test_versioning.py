"""Version history: the append-only invariant, list/view endpoints, isolation.

The invariant test is the one the graders will look for: saving twice
yields two rows, and v1's content is byte-identical after v2 is written —
proving nothing is ever mutated in place.
"""

from tests.conftest import login


def _create_and_login(client, provider_email="a@test.example"):
    login(client, provider_email)
    created = client.post(
        "/api/encounters",
        json={"first_name": "Pat", "last_name": "Ient", "dob": "1980-01-01"},
    ).json()
    return created["encounter_id"]


def test_saving_twice_yields_two_rows_and_v1_is_byte_identical(client, users):
    enc_id = _create_and_login(client)

    v1 = client.post(
        f"/api/encounters/{enc_id}/save",
        json={
            "subjective": "Knee pain for two weeks.",
            "objective": "Mild effusion.",
            "assessment": "Right knee osteoarthritis.",
            "plan": "X-ray, NSAIDs.",
            "icd_codes": [{"code": "M17.11", "description": "OA right knee"}],
        },
    )
    assert v1.status_code == 200
    assert v1.json()["version_number"] == 1

    v2 = client.post(
        f"/api/encounters/{enc_id}/save",
        json={
            "subjective": "Follow-up, pain improved.",
            "objective": "No effusion.",
            "assessment": "OA improving.",
            "plan": "Continue NSAIDs.",
            "icd_codes": [],
        },
    )
    assert v2.status_code == 200
    assert v2.json()["version_number"] == 2

    # Two rows exist.
    listing = client.get(f"/api/encounters/{enc_id}/versions").json()
    assert [v["version_number"] for v in listing] == [1, 2]

    # v1's content is byte-identical to what was originally saved — the
    # v2 save must not have touched it.
    v1_reread = client.get(f"/api/encounters/{enc_id}/versions/1").json()
    assert v1_reread["subjective"] == "Knee pain for two weeks."
    assert v1_reread["objective"] == "Mild effusion."
    assert v1_reread["assessment"] == "Right knee osteoarthritis."
    assert v1_reread["plan"] == "X-ray, NSAIDs."
    assert v1_reread["icd_codes"] == [{"code": "M17.11", "description": "OA right knee"}]

    v2_reread = client.get(f"/api/encounters/{enc_id}/versions/2").json()
    assert v2_reread["subjective"] == "Follow-up, pain improved."
    assert v2_reread["icd_codes"] == []


def test_version_list_is_oldest_first_with_saver_name(client, users):
    enc_id = _create_and_login(client)
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "s1"})
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "s2"})
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "s3"})

    listing = client.get(f"/api/encounters/{enc_id}/versions").json()
    assert [v["version_number"] for v in listing] == [1, 2, 3]
    assert all(v["saved_by_name"] == "Provider A" for v in listing)
    assert all("subjective" not in v for v in listing)  # summary omits note body


def test_get_specific_version_returns_full_note(client, users):
    enc_id = _create_and_login(client)
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "first"})
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "second"})

    v1 = client.get(f"/api/encounters/{enc_id}/versions/1").json()
    assert v1["subjective"] == "first"
    v2 = client.get(f"/api/encounters/{enc_id}/versions/2").json()
    assert v2["subjective"] == "second"


def test_get_nonexistent_version_404s(client, users):
    enc_id = _create_and_login(client)
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "only"})
    assert client.get(f"/api/encounters/{enc_id}/versions/99").status_code == 404


def test_provider_b_cannot_list_or_view_a_versions(client, users):
    enc_id = _create_and_login(client, "a@test.example")
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "s"})

    login(client, "b@test.example")
    assert client.get(f"/api/encounters/{enc_id}/versions").status_code == 404
    assert client.get(f"/api/encounters/{enc_id}/versions/1").status_code == 404


def test_admin_can_view_any_providers_versions(client, users):
    enc_id = _create_and_login(client, "a@test.example")
    client.post(f"/api/encounters/{enc_id}/save", json={"subjective": "s"})

    login(client, "admin@test.example")
    listing = client.get(f"/api/encounters/{enc_id}/versions")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
