"""Voice-edit WebSocket route — MOCKED LLM (deterministic, fast, free).

The mock replaces app.llm.complete_json — the exact seam the route uses —
so these tests exercise the real WebSocket auth/isolation, the real
apply_note_patch validation, and the real draft_note persistence with zero
vendor traffic.
"""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from app import llm
from app.main import app
from app.models import Encounter
from tests.conftest import login


def make_fake_complete_json(response_text=None, responses=None, kind="ok"):
    """Build a complete_json double. `responses` (a list) is consumed one
    per call for multi-command tests; `response_text` is used for every
    call otherwise."""
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        if responses is not None:
            text = responses[len(calls) - 1]
        else:
            text = response_text
        return kind, text

    fake.calls = calls
    return fake


@pytest.fixture
def encounter_with_draft(client, users, db):
    login(client, "a@test.example")
    created = client.post(
        "/api/encounters",
        json={"first_name": "Pat", "last_name": "Ient", "dob": "1980-01-01"},
    ).json()
    encounter_id = created["encounter_id"]
    client.patch(
        f"/api/encounters/{encounter_id}",
        json={
            "transcript": "knee pain",
            "draft_note": {
                "subjective": "Patient reports knee pain for two weeks.",
                "objective": "Mild swelling noted.",
                "assessment": "Right knee pain, etiology unclear.",
                "plan": "1. Start physical therapy.\n2. Follow up in six weeks.",
                "icd_codes": [{"code": "M25.561", "description": "Pain in right knee"}],
            },
        },
    )
    return encounter_id


def _ws_url(encounter_id: int) -> str:
    return f"/ws/encounters/{encounter_id}/voice-edit"


# ---- auth / isolation -----------------------------------------------------


def test_unauthenticated_connection_is_rejected(encounter_with_draft):
    # A fresh, cookie-less client — `client` in this file's other tests is
    # already authenticated by the time encounter_with_draft's own setup
    # (which creates the encounter as provider A) has run.
    anonymous = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with anonymous.websocket_connect(_ws_url(encounter_with_draft)):
            pass


def test_other_providers_encounter_is_rejected(client, users, encounter_with_draft):
    login(client, "b@test.example")  # provider B, not the owner
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(_ws_url(encounter_with_draft)):
            pass


def test_nonexistent_encounter_is_rejected(client, users):
    login(client, "a@test.example")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(_ws_url(999999)):
            pass


# ---- happy path -------------------------------------------------------------


def test_add_command_applies_patch_and_persists(client, encounter_with_draft, monkeypatch, db):
    login(client, "a@test.example")
    fake = make_fake_complete_json(
        '{"op": "add", "section": "assessment", "text": "Denies fever."}'
    )
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "add denies fever to the assessment"})
        reply = ws.receive_json()

    assert reply["type"] == "patch_applied"
    assert reply["note"]["assessment"] == "Right knee pain, etiology unclear. Denies fever."
    assert reply["note"]["subjective"] == "Patient reports knee pain for two weeks."
    assert "Added to assessment" in reply["message"]

    # sonnet tier, per llm.py's own settled tier rationale for voice edits.
    assert fake.calls[0]["model"] == llm.MODEL_FINAL

    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["assessment"] == "Right knee pain, etiology unclear. Denies fever."


def test_icd_codes_survive_a_voice_edit(client, encounter_with_draft, monkeypatch, db):
    login(client, "a@test.example")
    fake = make_fake_complete_json(
        '{"op": "add", "section": "plan", "text": "Order X-ray."}'
    )
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "add order an x-ray to the plan"})
        ws.receive_json()

    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["icd_codes"] == [
        {"code": "M25.561", "description": "Pain in right knee"}
    ]


def test_move_command_relocates_text(client, encounter_with_draft, monkeypatch):
    login(client, "a@test.example")
    fake = make_fake_complete_json(json.dumps({
        "op": "move",
        "from_section": "subjective",
        "to_section": "objective",
        "text": "for two weeks",
    }))
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "move the two weeks detail to objective"})
        reply = ws.receive_json()

    assert reply["type"] == "patch_applied"
    assert "for two weeks" not in reply["note"]["subjective"]
    assert "for two weeks" in reply["note"]["objective"]


def test_multiple_consecutive_commands_processed_in_order(client, encounter_with_draft, monkeypatch):
    login(client, "a@test.example")
    fake = make_fake_complete_json(responses=[
        '{"op": "add", "section": "assessment", "text": "First addition."}',
        '{"op": "add", "section": "assessment", "text": "Second addition."}',
    ])
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "add first addition to assessment"})
        first = ws.receive_json()
        ws.send_json({"type": "command", "text": "add second addition to assessment"})
        second = ws.receive_json()

    assert first["note"]["assessment"].endswith("First addition.")
    # Second command's patch was generated from the note state AFTER the
    # first was applied — proving commands are serialized, not raced.
    assert second["note"]["assessment"].endswith("First addition. Second addition.")


# ---- graceful error handling -----------------------------------------------


def test_malformed_client_message_returns_error_and_keeps_connection_open(
    client, encounter_with_draft, monkeypatch
):
    login(client, "a@test.example")
    fake = make_fake_complete_json(
        '{"op": "add", "section": "plan", "text": "Refer to PT."}'
    )
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_text("not even json")
        error_reply = ws.receive_json()
        assert error_reply["type"] == "error"

        # Connection must still be usable for the next, well-formed command.
        ws.send_json({"type": "command", "text": "add refer to PT to the plan"})
        ok_reply = ws.receive_json()
        assert ok_reply["type"] == "patch_applied"


def test_empty_command_text_returns_error(client, encounter_with_draft, monkeypatch):
    login(client, "a@test.example")
    fake = make_fake_complete_json("irrelevant")
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "   "})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    assert fake.calls == []  # never even reached the LLM


def test_llm_error_surfaces_as_graceful_error(client, encounter_with_draft, monkeypatch, db):
    login(client, "a@test.example")
    fake = make_fake_complete_json(llm.USER_FACING_FAILURE, kind="error")
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "add something"})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    assert reply["message"] == llm.USER_FACING_FAILURE
    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["assessment"] == "Right knee pain, etiology unclear."


def test_malformed_model_json_returns_error_and_does_not_mutate(
    client, encounter_with_draft, monkeypatch, db
):
    login(client, "a@test.example")
    fake = make_fake_complete_json("this is not json at all")
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "do something unclear"})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["assessment"] == "Right knee pain, etiology unclear."


def test_model_wraps_patch_in_markdown_fence_is_still_parsed(client, encounter_with_draft, monkeypatch):
    login(client, "a@test.example")
    fake = make_fake_complete_json(
        '```json\n{"op": "add", "section": "plan", "text": "Refer to PT."}\n```'
    )
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "add refer to PT to plan"})
        reply = ws.receive_json()

    assert reply["type"] == "patch_applied"
    assert "Refer to PT." in reply["note"]["plan"]


def test_unclear_command_returns_friendly_error(client, encounter_with_draft, monkeypatch, db):
    login(client, "a@test.example")
    fake = make_fake_complete_json('{"op": "unclear"}')
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "um, what was I saying"})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    assert "try again" in reply["message"].lower()
    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["assessment"] == "Right knee pain, etiology unclear."


def test_invalid_patch_op_returns_error_and_does_not_mutate(client, encounter_with_draft, monkeypatch, db):
    login(client, "a@test.example")
    fake = make_fake_complete_json('{"op": "delete", "section": "plan", "text": "x"}')
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "delete the plan"})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["plan"] == "1. Start physical therapy.\n2. Follow up in six weeks."


def test_remove_text_not_found_returns_error_and_does_not_mutate(
    client, encounter_with_draft, monkeypatch, db
):
    login(client, "a@test.example")
    fake = make_fake_complete_json(
        '{"op": "remove", "section": "assessment", "text": "text that was never in the note"}'
    )
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "remove the nonexistent phrase"})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    assert "verbatim" in reply["message"].lower() or "not found" in reply["message"].lower()
    db.expire_all()
    encounter = db.get(Encounter, encounter_with_draft)
    assert encounter.draft_note["assessment"] == "Right knee pain, etiology unclear."


# ---- audit ------------------------------------------------------------------


def test_successful_patch_is_audited(client, encounter_with_draft, monkeypatch, db):
    login(client, "a@test.example")
    fake = make_fake_complete_json(
        '{"op": "add", "section": "plan", "text": "Refer to PT."}'
    )
    monkeypatch.setattr(llm, "complete_json", fake)

    with client.websocket_connect(_ws_url(encounter_with_draft)) as ws:
        ws.send_json({"type": "command", "text": "add refer to PT to plan"})
        ws.receive_json()

    from app.models import AuditLog

    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.action == "voice_edit_patch").all()
    assert len(rows) == 1
    assert rows[0].entity_id == encounter_with_draft
