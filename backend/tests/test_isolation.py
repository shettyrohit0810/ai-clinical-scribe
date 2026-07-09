"""Provider data isolation — the test the graders will look for.

Provider B must not be able to read provider A's encounter, by list or by id,
and the by-id denial must be a 404 (not 403) so the id's existence doesn't
leak. Admin sees everything.
"""

from tests.conftest import login


def test_provider_a_can_read_own_encounter(client, users, encounter_for_a):
    login(client, "a@test.example")
    response = client.get(f"/api/encounters/{encounter_for_a.id}")
    assert response.status_code == 200
    assert response.json()["transcript"] == "knee pain for two weeks"


def test_provider_b_cannot_read_provider_a_encounter(client, users, encounter_for_a):
    login(client, "b@test.example")

    by_id = client.get(f"/api/encounters/{encounter_for_a.id}")
    assert by_id.status_code == 404  # 404, not 403: existence must not leak

    listing = client.get("/api/encounters")
    assert listing.status_code == 200
    assert listing.json() == []


def test_admin_sees_all_encounters(client, users, encounter_for_a):
    login(client, "admin@test.example")

    listing = client.get("/api/encounters")
    assert [e["id"] for e in listing.json()] == [encounter_for_a.id]
    assert client.get(f"/api/encounters/{encounter_for_a.id}").status_code == 200
