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
from collections.abc import AsyncIterator

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
