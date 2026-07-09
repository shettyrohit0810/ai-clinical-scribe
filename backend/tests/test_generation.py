"""Generation endpoint with a MOCKED LLM (deterministic, fast, free).

The mock replaces app.llm.stream_completion — the exact seam the route
uses — so these tests exercise the real parser, the real SSE framing, and
the real refusal/error handling with zero vendor traffic.
"""

import pytest

from app import llm
from tests.conftest import login

FAKE_NOTE = (
    "<subjective>Knee pain.</subjective>"
    "<objective>Effusion.</objective>"
    "<assessment>OA (M17.11).</assessment>"
    "<plan>X-ray.</plan>"
    '<icd_codes>[{"code": "M17.11", "description": "OA right knee"}]</icd_codes>'
)


def make_fake_stream(chunks=None, error_after=None):
    """Build a stream_completion double yielding the given chunks in 7-char
    pieces (so tag-splitting happens in-flight too)."""

    async def fake(**kwargs):
        fake.calls.append(kwargs)
        text = "".join(chunks) if chunks else FAKE_NOTE
        emitted = 0
        for i in range(0, len(text), 7):
            if error_after is not None and emitted >= error_after:
                yield "error", llm.USER_FACING_FAILURE
                return
            yield "delta", text[i : i + 7]
            emitted += 1
        yield "end", None

    fake.calls = []
    return fake


def sse_events(response):
    """Parse an SSE body into (event, data-json-string) pairs."""
    events = []
    current = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: ") and current:
            events.append((current, line[6:]))
            current = None
    return events


@pytest.fixture
def encounter_id(client, users):
    login(client, "a@test.example")
    created = client.post(
        "/api/encounters",
        json={"first_name": "Pat", "last_name": "Ient", "dob": "1980-01-01"},
    ).json()
    client.patch(
        f"/api/encounters/{created['encounter_id']}",
        json={"transcript": "patient reports right knee pain for two weeks"},
    )
    return created["encounter_id"]


def test_generate_streams_sections_and_codes(client, encounter_id, monkeypatch):
    fake = make_fake_stream()
    monkeypatch.setattr(llm, "stream_completion", fake)

    response = client.get(f"/api/encounters/{encounter_id}/generate")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = sse_events(response)
    names = [e for e, _ in events]
    assert names[-1] == "done"
    assert "icd_codes" in names

    subjective = "".join(
        __import__("json").loads(d)["delta"]
        for e, d in events
        if e == "section" and '"subjective"' in d
    )
    assert subjective == "Knee pain."

    # Default tier is the quality budget (sonnet).
    assert fake.calls[0]["model"] == llm.MODEL_FINAL


def test_draft_tier_uses_haiku(client, encounter_id, monkeypatch):
    fake = make_fake_stream()
    monkeypatch.setattr(llm, "stream_completion", fake)
    client.get(f"/api/encounters/{encounter_id}/generate?tier=draft")
    assert fake.calls[0]["model"] == llm.MODEL_DRAFT


def test_candidates_are_injected_into_prompt(client, encounter_id, monkeypatch, db):
    from app.models import IcdCode
    from app.icd import embed_text

    db.add(IcdCode(code="M25.561", description="Pain in right knee",
                   embedding=embed_text("Pain in right knee")))
    db.add(IcdCode(code="Z23", description="Encounter for immunization",
                   embedding=embed_text("Encounter for immunization")))
    db.commit()

    fake = make_fake_stream()
    monkeypatch.setattr(llm, "stream_completion", fake)
    client.get(f"/api/encounters/{encounter_id}/generate")

    prompt = fake.calls[0]["user_prompt"]
    assert "CANDIDATE ICD-10 CODES" in prompt
    assert "M25.561" in prompt  # knee-pain transcript retrieved the knee code


def test_no_clinical_content_refusal(client, encounter_id, monkeypatch):
    fake = make_fake_stream(chunks=["<no_clinical_content/>"])
    monkeypatch.setattr(llm, "stream_completion", fake)

    events = sse_events(client.get(f"/api/encounters/{encounter_id}/generate"))
    assert ("no_clinical_content", "{}") in events
    assert events[-1][0] == "done"


def test_empty_transcript_short_circuits_without_llm_call(client, users, monkeypatch):
    login(client, "a@test.example")
    enc = client.post(
        "/api/encounters",
        json={"first_name": "No", "last_name": "Script", "dob": "1990-01-01"},
    ).json()

    fake = make_fake_stream()
    monkeypatch.setattr(llm, "stream_completion", fake)
    events = sse_events(client.get(f"/api/encounters/{enc['encounter_id']}/generate"))

    assert ("no_clinical_content", "{}") in events
    assert fake.calls == []  # no LLM spend on a certain refusal


def test_llm_failure_becomes_calm_error_event(client, encounter_id, monkeypatch):
    fake = make_fake_stream(error_after=2)
    monkeypatch.setattr(llm, "stream_completion", fake)

    events = sse_events(client.get(f"/api/encounters/{encounter_id}/generate"))
    assert events[-1][0] == "error"
    assert "transcript is safe" in events[-1][1]
