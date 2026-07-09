"""History block builder + generation-route tool-use behavior (mocked LLM)."""

import pytest

from app import llm
from app.history import build_history_block, count_prior_saved
from app.models import Encounter, EncounterStatus, NoteVersion, Patient
from tests.conftest import login
from tests.test_generation import sse_events


@pytest.fixture
def patient_with_history(db, users):
    """One patient, two prior SAVED encounters (v1 notes), one prior draft
    (must be ignored), and a fresh current draft encounter."""
    provider = users["provider_a"]
    patient = Patient(first_name="Margaret", last_name="Thompson", dob="1954-03-17")
    db.add(patient)
    db.flush()

    saved_ids = []
    for i, (assessment, plan) in enumerate(
        [("Right knee OA (M17.11).", "Start PT."), ("OA improving.", "Continue PT.")]
    ):
        enc = Encounter(
            patient_id=patient.id, provider_id=provider.id,
            transcript=f"visit {i}", status=EncounterStatus.saved,
        )
        db.add(enc)
        db.flush()
        db.add(NoteVersion(
            encounter_id=enc.id, version_number=1,
            subjective=f"Subjective {i}", assessment=assessment, plan=plan,
            saved_by=provider.id,
        ))
        saved_ids.append(enc.id)

    draft = Encounter(  # prior DRAFT — no signed note, must not appear
        patient_id=patient.id, provider_id=provider.id,
        transcript="unsaved", status=EncounterStatus.draft,
    )
    current = Encounter(
        patient_id=patient.id, provider_id=provider.id,
        transcript="today: knee pain follow-up", status=EncounterStatus.draft,
    )
    db.add_all([draft, current])
    db.commit()
    return current


def test_count_and_block_use_saved_only(db, patient_with_history):
    current = patient_with_history
    assert count_prior_saved(db, current) == 2  # drafts and self excluded

    block = build_history_block(db, current)
    assert "Right knee OA (M17.11)" in block
    assert "OA improving." in block
    assert "unsaved" not in block
    assert "today: knee pain" not in block
    assert block.count("--- Encounter on") == 2


def test_new_patient_block_is_explicit(db, users):
    patient = Patient(first_name="New", last_name="Person", dob="1999-01-01")
    db.add(patient)
    db.flush()
    enc = Encounter(patient_id=patient.id, provider_id=users["provider_a"].id)
    db.add(enc)
    db.commit()
    assert count_prior_saved(db, enc) == 0
    assert "No prior encounters" in build_history_block(db, enc)


# ---- generation route: tool events -------------------------------------------


NOTE_WITH_HISTORY = (
    "<subjective>Follow-up, improved since last visit.</subjective>"
    "<objective>Stable.</objective><assessment>OA improving.</assessment>"
    "<plan>Continue.</plan><icd_codes>[]</icd_codes>"
)


def make_fake_note_generation(*, call_tool: bool, reset: bool = False):
    """Double for llm.stream_note_generation that optionally simulates a
    tool round (invoking the provider like the real loop does)."""

    async def fake(**kwargs):
        fake.calls.append(kwargs)
        provider = kwargs.get("history_provider")
        if call_tool and provider is not None:
            if reset:
                yield "delta", "<subjective>premature"
                yield "reset", None
            yield "tool_called", None
            fake.tool_results.append(provider())  # server-side fetch
        for i in range(0, len(NOTE_WITH_HISTORY), 9):
            yield "delta", NOTE_WITH_HISTORY[i : i + 9]
        yield "end", None

    fake.calls = []
    fake.tool_results = []
    return fake


def test_returning_patient_gets_history_event_and_audit(
    client, db, patient_with_history, monkeypatch
):
    fake = make_fake_note_generation(call_tool=True)
    monkeypatch.setattr(llm, "stream_note_generation", fake)
    login(client, "a@test.example")

    events = sse_events(
        client.get(f"/api/encounters/{patient_with_history.id}/generate")
    )

    assert ("history", '{"prior_encounters": 2}') in events
    assert events[-1][0] == "done"
    # Route passed a real provider; the fake invoked it → history text flowed.
    assert "Right knee OA" in fake.tool_results[0]
    # Prompt told the model history exists and to call the tool first.
    assert "fetch_patient_history" in fake.calls[0]["user_prompt"]

    # The invocation is audited — showable on camera.
    from app.models import AuditLog
    from sqlalchemy import select
    row = db.scalar(select(AuditLog).where(
        AuditLog.action == "tool_call:fetch_patient_history"
    ))
    assert row is not None
    assert row.entity_id == patient_with_history.id


def test_new_patient_gets_no_tool_and_no_history_event(client, users, monkeypatch):
    fake = make_fake_note_generation(call_tool=True)  # would call if offered
    monkeypatch.setattr(llm, "stream_note_generation", fake)
    login(client, "a@test.example")
    created = client.post("/api/encounters", json={
        "first_name": "Brand", "last_name": "New", "dob": "2000-05-05",
    }).json()
    client.patch(f"/api/encounters/{created['encounter_id']}",
                 json={"transcript": "sore throat two days"})

    events = sse_events(
        client.get(f"/api/encounters/{created['encounter_id']}/generate")
    )

    assert fake.calls[0]["history_provider"] is None  # tool never offered
    assert all(e != "history" for e, _ in events)
    assert "fetch_patient_history" not in fake.calls[0]["user_prompt"]


def test_reset_event_restarts_parsing(client, db, patient_with_history, monkeypatch):
    fake = make_fake_note_generation(call_tool=True, reset=True)
    monkeypatch.setattr(llm, "stream_note_generation", fake)
    login(client, "a@test.example")

    response = client.get(f"/api/encounters/{patient_with_history.id}/generate")
    events = sse_events(response)

    names = [e for e, _ in events]
    assert "reset" in names
    assert names.index("reset") < names.index("history")
    # Post-reset subjective must be ONLY the real note's text — the
    # pre-tool fragment was parsed by the discarded parser.
    import json as _json
    post_reset = events[names.index("reset"):]
    subj = "".join(
        _json.loads(d)["delta"] for e, d in post_reset
        if e == "section" and '"subjective"' in d
    )
    assert subj == "Follow-up, improved since last visit."
