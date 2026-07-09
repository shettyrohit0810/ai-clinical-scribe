"""stream_note_generation's tool loop against a scripted fake SDK client.

Verifies the loop mechanics the route tests can't see: tool_result plumbing
back into messages, the reset-on-premature-text rule, round limits, and
error containment — all without vendor traffic.
"""

from types import SimpleNamespace

import pytest

from app import llm

# anyio's pytest plugin runs these async tests on asyncio.
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeStream:
    """Stands in for the SDK's messages.stream() context manager."""

    def __init__(self, texts: list[str], final):
        self._texts = texts
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for t in self._texts:
            yield t

    async def get_final_message(self):
        return self._final


def final_message(stop_reason: str, *, with_tool_use: bool = False):
    content = []
    if with_tool_use:
        content.append(SimpleNamespace(type="tool_use", id="toolu_1", name="fetch_patient_history", input={}))
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


class FakeClient:
    """Pops one scripted FakeStream per round; records request kwargs."""

    def __init__(self, script: list[FakeStream]):
        self._script = list(script)
        self.requests: list[dict] = []
        self.messages = self  # so client.messages.stream(...) resolves here

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        return self._script.pop(0)


async def run(gen):
    return [event async for event in gen]


@pytest.fixture
def use_fake_client(monkeypatch):
    def install(script):
        fake = FakeClient(script)
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        return fake
    return install


async def test_tool_round_plumbs_result_and_streams_final(use_fake_client):
    fake = use_fake_client([
        FakeStream([], final_message("tool_use", with_tool_use=True)),
        FakeStream(["<plan>ok</plan>"], final_message("end_turn")),
    ])
    provider_calls = []

    def provider():
        provider_calls.append(1)
        return "HISTORY BLOCK"

    events = await run(llm.stream_note_generation(
        model="m", system="s", user_prompt="u", history_provider=provider,
    ))

    assert events == [("tool_called", None), ("delta", "<plan>ok</plan>"), ("end", None)]
    assert provider_calls == [1]
    # Round 2 request carries assistant tool_use turn + our tool_result.
    second = fake.requests[1]["messages"]
    assert second[1]["role"] == "assistant"
    assert second[2]["content"][0]["type"] == "tool_result"
    assert second[2]["content"][0]["content"] == "HISTORY BLOCK"
    assert second[2]["content"][0]["tool_use_id"] == "toolu_1"
    # Tool offered on every round.
    assert all(r["tools"] == [llm.FETCH_HISTORY_TOOL] for r in fake.requests)


async def test_text_before_tool_call_triggers_reset(use_fake_client):
    use_fake_client([
        FakeStream(["premature text"], final_message("tool_use", with_tool_use=True)),
        FakeStream(["<plan>real</plan>"], final_message("end_turn")),
    ])
    events = await run(llm.stream_note_generation(
        model="m", system="s", user_prompt="u", history_provider=lambda: "H",
    ))
    assert events == [
        ("delta", "premature text"),
        ("reset", None),
        ("tool_called", None),
        ("delta", "<plan>real</plan>"),
        ("end", None),
    ]


async def test_runaway_tool_rounds_become_structured_error(use_fake_client):
    use_fake_client([
        FakeStream([], final_message("tool_use", with_tool_use=True))
        for _ in range(3)
    ])
    events = await run(llm.stream_note_generation(
        model="m", system="s", user_prompt="u", history_provider=lambda: "H",
    ))
    assert events[-1] == ("error", llm.USER_FACING_FAILURE)


async def test_sdk_exception_becomes_structured_error(use_fake_client):
    class Boom:
        def stream(self, **kwargs):
            raise RuntimeError("vendor down")

    boom = Boom()
    boom.messages = boom
    import unittest.mock
    with unittest.mock.patch.object(llm, "_get_client", lambda: boom):
        events = await run(llm.stream_note_generation(
            model="m", system="s", user_prompt="u", history_provider=lambda: "H",
        ))
    assert events == [("error", llm.USER_FACING_FAILURE)]


async def test_no_provider_delegates_to_plain_stream(monkeypatch):
    async def fake_plain(**kwargs):
        fake_plain.kwargs = kwargs
        yield "delta", "x"
        yield "end", None

    monkeypatch.setattr(llm, "stream_completion", fake_plain)
    events = await run(llm.stream_note_generation(
        model="m", system="s", user_prompt="u", history_provider=None,
    ))
    assert events == [("delta", "x"), ("end", None)]
    assert "tools" not in fake_plain.kwargs  # no tool offered to new patients
