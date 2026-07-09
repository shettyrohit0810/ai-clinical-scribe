"""Admin encounter filters — extending GET /api/encounters, not duplicating it.

The isolation guarantee already covered in test_isolation.py must survive
this extension: a provider passing provider_id / date filters must still
see only their own encounters.
"""

import itertools
from datetime import date, timedelta

from app.models import Encounter, EncounterStatus, Patient
from tests.conftest import login

_patient_seq = itertools.count(1)


def _make_encounter(db, provider, days_ago=0):
    # Unique identity per call: patients has a composite UNIQUE(first,
    # last, dob) — the returning-patient matcher — so two encounters in the
    # same test must not collide on it.
    n = next(_patient_seq)
    patient = Patient(first_name="Pat", last_name=f"Ient{n}", dob="1980-01-01")
    db.add(patient)
    db.flush()
    when = date.today() - timedelta(days=days_ago)
    encounter = Encounter(
        patient_id=patient.id,
        provider_id=provider.id,
        status=EncounterStatus.saved,
        created_at=when,
    )
    db.add(encounter)
    db.commit()
    return encounter


def test_encounter_summary_includes_provider_identity(client, users, encounter_for_a):
    login(client, "a@test.example")
    row = client.get("/api/encounters").json()[0]
    assert row["provider_id"] == users["provider_a"].id
    assert row["provider_name"] == "Provider A"


def test_admin_sees_all_encounters_unfiltered(client, db, users):
    _make_encounter(db, users["provider_a"])
    _make_encounter(db, users["provider_b"])
    login(client, "admin@test.example")

    rows = client.get("/api/encounters").json()
    assert len(rows) == 2
    assert {r["provider_name"] for r in rows} == {"Provider A", "Provider B"}


def test_admin_provider_filter(client, db, users):
    _make_encounter(db, users["provider_a"])
    _make_encounter(db, users["provider_b"])
    login(client, "admin@test.example")

    rows = client.get(
        "/api/encounters", params={"provider_id": users["provider_a"].id}
    ).json()
    assert len(rows) == 1
    assert rows[0]["provider_name"] == "Provider A"


def test_admin_date_range_filter(client, db, users):
    _make_encounter(db, users["provider_a"], days_ago=10)
    _make_encounter(db, users["provider_a"], days_ago=1)
    login(client, "admin@test.example")

    recent = client.get(
        "/api/encounters",
        params={"date_from": (date.today() - timedelta(days=3)).isoformat()},
    ).json()
    assert len(recent) == 1

    older = client.get(
        "/api/encounters",
        params={"date_to": (date.today() - timedelta(days=5)).isoformat()},
    ).json()
    assert len(older) == 1
    assert older[0] != recent[0]


def test_provider_cannot_use_provider_id_filter_to_see_others(client, db, users):
    """provider_id is an elif under the isolation branch, so for a
    non-admin caller it is inert (the isolation `.where()` already matched
    and short-circuited) rather than combined with it — a provider always
    sees exactly their own encounters, no matter what id they pass."""
    _make_encounter(db, users["provider_a"])
    _make_encounter(db, users["provider_b"])
    login(client, "a@test.example")

    rows = client.get(
        "/api/encounters", params={"provider_id": users["provider_b"].id}
    ).json()
    assert len(rows) == 1
    assert rows[0]["provider_name"] == "Provider A"  # never provider B's data
