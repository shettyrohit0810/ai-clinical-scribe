"""Backend integration tests (Phase 10).

Every other test file exercises one feature/router in relative isolation.
These instead walk full, realistic multi-step workflows that cross several
routers in a single test — proving the pieces actually compose, not just
that each one works alone. LLM calls stay mocked (deterministic, fast,
free, same seam every other test file uses); everything else — auth,
isolation, the real test DB, the real SSE/WS wiring — is exercised for
real through the same TestClient every other suite uses.
"""

from sqlalchemy import select

from app import llm
from app.models import AuditLog
from tests.conftest import TEST_PASSWORD, login
from tests.test_generation import make_fake_stream, sse_events
from tests.test_voice_edit import make_fake_complete_json


# ---- Scenario 1: full single-encounter lifecycle ---------------------------
#
# create -> autosave -> generate -> save v1 -> voice-edit -> save v2 ->
# version history shows both, v1 immutable, audit trail complete and
# ordered.


def test_full_encounter_lifecycle_across_features(client, users, db, monkeypatch):
    login(client, "a@test.example")

    created = client.post(
        "/api/encounters",
        json={"first_name": "Pat", "last_name": "Ient", "dob": "1980-01-01"},
    ).json()
    encounter_id = created["encounter_id"]
    assert created["returning"] is False

    client.patch(
        f"/api/encounters/{encounter_id}",
        json={"transcript": "knee pain for two weeks, worse on stairs"},
    )

    fake_generate = make_fake_stream(chunks=[
        "<subjective>Knee pain for two weeks.</subjective>"
        "<objective>Mild swelling.</objective>"
        "<assessment>Right knee pain (M25.561).</assessment>"
        "<plan>Start physical therapy.</plan>"
        '<icd_codes>[{"code": "M25.561", "description": "Pain in right knee"}]</icd_codes>'
    ])
    monkeypatch.setattr(llm, "stream_completion", fake_generate)

    events = sse_events(client.get(f"/api/encounters/{encounter_id}/generate"))
    assert events[-1][0] == "done"

    save1 = client.post(
        f"/api/encounters/{encounter_id}/save",
        json={
            "subjective": "Knee pain for two weeks.",
            "objective": "Mild swelling.",
            "assessment": "Right knee pain (M25.561).",
            "plan": "Start physical therapy.",
            "icd_codes": [{"code": "M25.561", "description": "Pain in right knee"}],
        },
    )
    assert save1.status_code == 200
    assert save1.json()["version_number"] == 1

    # Voice-edit the just-saved note. The route reads FRESH state, so it
    # must pick up v1's content (no draft_note exists yet — save() cleared
    # it) via the latest-saved-version fallback in voice_edit._current_note.
    fake_patch = make_fake_complete_json(
        '{"op": "add", "section": "assessment", "text": "Denies fever."}'
    )
    monkeypatch.setattr(llm, "complete_json", fake_patch)
    with client.websocket_connect(f"/ws/encounters/{encounter_id}/voice-edit") as ws:
        ws.send_json({"type": "command", "text": "add denies fever to assessment"})
        reply = ws.receive_json()
    assert reply["type"] == "patch_applied"
    assert reply["note"]["assessment"] == "Right knee pain (M25.561). Denies fever."
    # icd_codes carried through untouched by the voice edit.

    save2 = client.post(
        f"/api/encounters/{encounter_id}/save",
        json={**reply["note"], "icd_codes": [{"code": "M25.561", "description": "Pain in right knee"}]},
    )
    assert save2.json()["version_number"] == 2

    versions = client.get(f"/api/encounters/{encounter_id}/versions").json()
    assert [v["version_number"] for v in versions] == [1, 2]

    v1 = client.get(f"/api/encounters/{encounter_id}/versions/1").json()
    assert v1["assessment"] == "Right knee pain (M25.561)."  # unchanged by the voice edit

    v2 = client.get(f"/api/encounters/{encounter_id}/versions/2").json()
    assert v2["assessment"] == "Right knee pain (M25.561). Denies fever."
    assert v2["icd_codes"] == [{"code": "M25.561", "description": "Pain in right knee"}]

    actions = db.scalars(
        select(AuditLog.action)
        .where(AuditLog.entity_id == encounter_id, AuditLog.entity_type == "encounter")
        .order_by(AuditLog.id)
    ).all()
    assert actions == ["encounter_create", "note_save", "voice_edit_patch", "note_save"]


# ---- Scenario 2: returning patient, history tool, admin visibility --------


def test_returning_patient_history_and_admin_visibility(client, users, db, monkeypatch):
    login(client, "a@test.example")

    first = client.post(
        "/api/encounters",
        json={"first_name": "Margaret", "last_name": "Thompson", "dob": "1954-03-17"},
    ).json()
    client.patch(f"/api/encounters/{first['encounter_id']}",
                 json={"transcript": "right knee OA follow-up"})
    fake_v1 = make_fake_stream(chunks=[
        "<subjective>OA follow-up.</subjective><objective>Stable.</objective>"
        "<assessment>OA right knee.</assessment><plan>Continue PT.</plan>"
        "<icd_codes>[]</icd_codes>"
    ])
    monkeypatch.setattr(llm, "stream_completion", fake_v1)
    sse_events(client.get(f"/api/encounters/{first['encounter_id']}/generate"))
    client.post(
        f"/api/encounters/{first['encounter_id']}/save",
        json={"subjective": "OA follow-up.", "objective": "Stable.",
              "assessment": "OA right knee.", "plan": "Continue PT.", "icd_codes": []},
    )

    # Same identity (case-insensitive match on first/last/dob) -> returning.
    second = client.post(
        "/api/encounters",
        json={"first_name": "margaret", "last_name": "thompson", "dob": "1954-03-17"},
    ).json()
    assert second["returning"] is True
    assert second["prior_encounters"] == 1
    assert second["patient"]["id"] == first["patient"]["id"]

    client.patch(f"/api/encounters/{second['encounter_id']}",
                 json={"transcript": "today: knee feels better"})

    async def fake_with_history(**kwargs):
        fake_with_history.calls.append(kwargs)
        provider = kwargs["history_provider"]
        yield "tool_called", None
        fake_with_history.tool_result = provider()
        yield "delta", "<subjective>Improved.</subjective><objective>Better.</objective>"
        yield "delta", "<assessment>OA improving.</assessment><plan>Continue.</plan>"
        yield "delta", "<icd_codes>[]</icd_codes>"
        yield "end", None

    fake_with_history.calls = []
    monkeypatch.setattr(llm, "stream_note_generation", fake_with_history)

    events = sse_events(client.get(f"/api/encounters/{second['encounter_id']}/generate"))
    assert ("history", '{"prior_encounters": 1}') in events
    assert "OA right knee" in fake_with_history.tool_result

    # Admin sees both of provider A's encounters via the SAME endpoint
    # providers use, filtered — no parallel admin-only route.
    login(client, "admin@test.example")
    all_for_provider = client.get(
        f"/api/encounters?provider_id={users['provider_a'].id}"
    ).json()
    ids = {e["id"] for e in all_for_provider}
    assert first["encounter_id"] in ids
    assert second["encounter_id"] in ids

    audit = client.get("/api/admin/audit?limit=50").json()
    audit_actions = [a["action"] for a in audit]
    assert "tool_call:fetch_patient_history" in audit_actions
    assert "encounter_create" in audit_actions
    assert "note_save" in audit_actions
    # Newest first.
    timestamps = [a["created_at"] for a in audit]
    assert timestamps == sorted(timestamps, reverse=True)


# ---- Scenario 3: deactivation mid-session, draft survives -----------------


def test_deactivation_mid_session_preserves_draft(client, users, db):
    login(client, "a@test.example")
    created = client.post(
        "/api/encounters",
        json={"first_name": "Pat", "last_name": "Ient", "dob": "1980-01-01"},
    ).json()
    encounter_id = created["encounter_id"]

    marker = "DEACTIVATION-TEST-MARKER text that must survive"
    patch = client.patch(
        f"/api/encounters/{encounter_id}",
        json={
            "transcript": "knee pain",
            "draft_note": {
                "subjective": marker, "objective": "", "assessment": "", "plan": "",
                "icd_codes": [],
            },
        },
    )
    assert patch.status_code == 200

    # Admin deactivates provider A WHILE that session's cookie is still
    # live in `client` — but logging in as admin overwrites the client's
    # cookie jar with the admin's session, so re-establish provider A's
    # session afterward to prove ITS next call (not a fresh login) 403s.
    provider_cookie = dict(client.cookies)
    login(client, "admin@test.example")
    deactivate = client.patch(
        f"/api/admin/providers/{users['provider_a'].id}", json={"is_active": False}
    )
    assert deactivate.status_code == 200
    admin_cookie = dict(client.cookies)

    # Restore provider A's original (still cryptographically valid) cookie
    # — this is the "already logged in, account deactivated out from under
    # them" scenario, not "tries to log in fresh".
    client.cookies.clear()
    client.cookies.update(provider_cookie)
    next_call = client.get(f"/api/encounters/{encounter_id}")
    assert next_call.status_code == 403
    assert next_call.json()["detail"] == "Account deactivated"

    # The draft is untouched — readable via the admin session, byte for byte.
    client.cookies.clear()
    client.cookies.update(admin_cookie)
    admin_view = client.get(f"/api/encounters/{encounter_id}")
    assert admin_view.status_code == 200
    assert admin_view.json()["draft_note"]["subjective"] == marker

    reactivate = client.patch(
        f"/api/admin/providers/{users['provider_a'].id}", json={"is_active": True}
    )
    assert reactivate.status_code == 200

    # Provider A logs in again fresh; draft is exactly where they left it.
    relogin = client.post(
        "/api/auth/login", json={"email": "a@test.example", "password": TEST_PASSWORD}
    )
    assert relogin.status_code == 200
    resumed = client.get(f"/api/encounters/{encounter_id}").json()
    assert resumed["draft_note"]["subjective"] == marker
