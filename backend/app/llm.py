"""The single gateway for ALL Anthropic API traffic (spec requirement).

Resilience contract — every property here is a walkthrough talking point:

- TWO TIERS (deliberate, settled decision): haiku for interim dictation
  drafts (latency/cost budget), sonnet for final notes and voice-edit
  commands (quality budget). Nothing else in the codebase names a model.
- Timeout: 60s per request. A hung vendor call must not hold an SSE
  connection (and its DB session) open indefinitely.
- One retry with backoff on transient failures: the SDK's built-in
  max_retries=1 retries connection errors, 408/429 and 5xx with
  exponential backoff — exactly the spec's "one retry", without
  hand-rolling retry logic the SDK already solves.
- Structured error results: stream_completion NEVER raises to its caller.
  Failures become a terminal ("error", user-safe-message) event, so routes
  turn them into a calm UI state ("Generation failed — your transcript is
  safe") instead of a 500. Drafts live in the DB; a vendor outage loses
  nothing.
- Cost control: max_tokens capped at MAX_OUTPUT_TOKENS; every call gets a
  per-process sequence number and is logged with its model and token usage.
"""

import itertools
import logging
from collections.abc import AsyncIterator, Callable

from anthropic import AsyncAnthropic

from app.config import get_settings

logger = logging.getLogger("app.llm")

# Tier constants — the ONLY place model IDs appear in the codebase.
MODEL_FINAL = "claude-sonnet-4-6"  # quality budget: final notes, voice edits
MODEL_DRAFT = "claude-haiku-4-5"  # latency/cost budget: interim dictation drafts

MAX_OUTPUT_TOKENS = 2000  # hard cap: a SOAP note fits comfortably; runaway costs don't

USER_FACING_FAILURE = "Generation failed — your transcript is safe. Try again."

_client: AsyncAnthropic | None = None
_call_counter = itertools.count(1)


def _get_client() -> AsyncAnthropic:
    """Lazy singleton: tests and CLI tooling can import this module (and the
    whole app) without an API key; only an actual call needs one."""
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=get_settings().anthropic_api_key,
            timeout=60.0,
            max_retries=1,  # the spec's "one retry with backoff", SDK-native
        )
    return _client


async def stream_completion(
    *,
    model: str,
    system: str,
    user_prompt: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> AsyncIterator[tuple[str, str | None]]:
    """Stream a completion as ("delta", text) events ending with ("end", None).

    On any failure — after the SDK's single retry — yields a terminal
    ("error", user_facing_message) instead of raising. Callers can rely on
    exactly one terminal event ("end" or "error").
    """
    call_no = next(_call_counter)
    logger.info("LLM call #%d start model=%s max_tokens=%d", call_no, model, max_tokens)
    try:
        async with _get_client().messages.stream(
            model=model,
            max_tokens=min(max_tokens, MAX_OUTPUT_TOKENS),
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield "delta", text
            final = await stream.get_final_message()
        logger.info(
            "LLM call #%d done in=%d out=%d",
            call_no,
            final.usage.input_tokens,
            final.usage.output_tokens,
        )
        yield "end", None
    except Exception:
        # Deliberately broad: whatever the vendor failure mode (auth, quota,
        # network, overload), the caller gets one structured error event and
        # the application stays fully usable.
        logger.exception("LLM call #%d failed model=%s", call_no, model)
        yield "error", USER_FACING_FAILURE


# ---- fetch_patient_history tool use (Phase 3) --------------------------------

# The tool deliberately takes NO arguments: the backend already knows which
# patient this generation is for and scopes the query server-side. The model
# cannot request any other patient's history — containment by construction.
FETCH_HISTORY_TOOL = {
    "name": "fetch_patient_history",
    "description": (
        "Fetch this patient's prior encounter notes (most recent saved "
        "visits: dates, subjective, assessment, plan). Call this before "
        "writing the note whenever prior history exists, so today's note can "
        "reference interval changes and prior findings."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_MAX_TOOL_ROUNDS = 3  # 1 tool round + final is the normal shape; hard stop


async def stream_note_generation(
    *,
    model: str,
    system: str,
    user_prompt: str,
    history_provider: Callable[[], str] | None = None,
) -> AsyncIterator[tuple[str, str | None]]:
    """Note generation with optional history tool use.

    history_provider=None (new patients): identical to stream_completion —
    no tool is offered, nothing to fetch, one round.

    With a provider (returning patients): the model is offered
    fetch_patient_history. When it calls the tool, the provider runs
    SERVER-SIDE, the result is appended as a tool_result turn, and the loop
    continues streaming. Extra events beyond stream_completion's:

        ("tool_called", None)  the tool ran — caller audits + notifies UI
        ("reset", None)        text was streamed before the tool call
                               (rare; prompt says call-first) — caller must
                               clear parser + panes before what follows

    Text deltas are forwarded optimistically for latency; "reset" is the
    safety net for the call-after-text reorder case.
    """
    if history_provider is None:
        async for event in stream_completion(
            model=model, system=system, user_prompt=user_prompt
        ):
            yield event
        return

    call_no = next(_call_counter)
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    emitted_text = False
    try:
        for round_no in range(1, _MAX_TOOL_ROUNDS + 1):
            logger.info(
                "LLM call #%d round %d start model=%s (history tool offered)",
                call_no, round_no, model,
            )
            async with _get_client().messages.stream(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=messages,
                tools=[FETCH_HISTORY_TOOL],
            ) as stream:
                async for text in stream.text_stream:
                    emitted_text = True
                    yield "delta", text
                final = await stream.get_final_message()
            logger.info(
                "LLM call #%d round %d done stop=%s in=%d out=%d",
                call_no, round_no, final.stop_reason,
                final.usage.input_tokens, final.usage.output_tokens,
            )

            if final.stop_reason != "tool_use":
                yield "end", None
                return

            tool_use = next(b for b in final.content if b.type == "tool_use")
            if emitted_text:
                yield "reset", None  # discard pre-tool text downstream
                emitted_text = False
            yield "tool_called", None
            result = history_provider()  # server-side fetch, logged by caller
            messages.append({"role": "assistant", "content": final.content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result,
                }],
            })

        logger.warning("LLM call #%d exceeded %d rounds", call_no, _MAX_TOOL_ROUNDS)
        yield "error", USER_FACING_FAILURE
    except Exception:
        logger.exception("LLM call #%d failed model=%s", call_no, model)
        yield "error", USER_FACING_FAILURE
