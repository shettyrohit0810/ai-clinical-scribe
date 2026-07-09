"""Note generation over SSE.

GET (not POST) because the browser consumes this with EventSource, which
only speaks GET. That works here because generation reads its inputs from
the DB — the client flushes its autosave PATCH first, then opens the
stream, so the server always generates from the freshest transcript.

Wire protocol (all data fields are JSON):
    event: section              {"section": "subjective", "delta": "..."}
    event: icd_codes            [{"code": "...", "description": "..."}]
    event: no_clinical_content  {}
    event: error                {"message": "..."}
    event: done                 {}
"""

import json
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import llm
from app.audit import record_audit
from app.auth import get_current_user
from app.db import get_db
from app.history import build_history_block, count_prior_saved
from app.icd import rank_candidates
from app.models import Template, User
from app.prompts import NOTE_SYSTEM, build_note_user_prompt
from app.routers.encounters import get_owned_encounter

logger = logging.getLogger("app.generation")

router = APIRouter(tags=["generation"])


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # belt-and-suspenders; nginx config is primary
}


@router.get("/encounters/{encounter_id}/generate")
async def generate_note(
    encounter_id: int,
    tier: str = Query("final", pattern="^(final|draft)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """tier=final → sonnet (quality budget, the default);
    tier=draft → haiku (latency budget, used by Phase 7 rolling dictation)."""
    encounter = get_owned_encounter(encounter_id, user, db)
    transcript = encounter.transcript.strip()

    # Empty transcript: the refusal outcome is certain, so don't spend an
    # LLM call discovering it.
    if not transcript:
        async def empty_stream():
            yield _sse("no_clinical_content", {})
            yield _sse("done", {})

        return StreamingResponse(
            empty_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    # TEMPLATE FRESHNESS BY DESIGN: instructions are read from the DB at
    # generation time — no cache, no push channel. An admin edit is simply
    # present on the next generate, which fully satisfies "next generation
    # uses the new template without refresh".
    template_instructions = None
    if encounter.template_id is not None:
        template = db.get(Template, encounter.template_id)
        if template is not None and template.is_active:
            template_instructions = template.instructions

    # Candidate-constrained ICD selection: the model chooses only from these.
    candidates = rank_candidates(db, transcript, k=8)

    # HISTORY TOOL (Phase 3): offered only when prior saved encounters exist.
    # New patients get a plain single-round stream — no tool, nothing to
    # fetch, and the returning-vs-new behavioral difference falls out of the
    # architecture rather than a prompt trick.
    prior_count = count_prior_saved(db, encounter)
    history_provider = None
    if prior_count > 0:
        def history_provider() -> str:
            # Executes when (and only when) the model calls the tool.
            # Audited so the server-side invocation is showable on camera.
            record_audit(
                db, user_id=user.id, action="tool_call:fetch_patient_history",
                entity_type="encounter", entity_id=encounter.id,
            )
            db.commit()
            block = build_history_block(db, encounter)
            logger.info(
                "fetch_patient_history executed: encounter=%d patient=%d "
                "prior_saved=%d chars=%d",
                encounter.id, encounter.patient_id, prior_count, len(block),
            )
            return block

    user_prompt = build_note_user_prompt(
        transcript=transcript,
        icd_candidates=candidates,
        template_instructions=template_instructions,
        history_available=prior_count > 0,
    )
    model = llm.MODEL_DRAFT if tier == "draft" else llm.MODEL_FINAL

    async def event_stream():
        # All routine DB reads happened above; history_provider is the one
        # deliberate exception (it runs only on an actual tool call).
        from app.stream_parser import TaggedStreamParser

        parser = TaggedStreamParser()
        async for kind, payload in llm.stream_note_generation(
            model=model, system=NOTE_SYSTEM, user_prompt=user_prompt,
            history_provider=history_provider,
        ):
            if kind == "delta":
                for name, section, data in parser.feed(payload):
                    if name == "section":
                        yield _sse("section", {"section": section, "delta": data})
                    elif name == "icd_codes":
                        yield _sse("icd_codes", data)
                    elif name == "no_clinical_content":
                        yield _sse("no_clinical_content", {})
            elif kind == "tool_called":
                # UI indicator: "History referenced: N prior encounters"
                yield _sse("history", {"prior_encounters": prior_count})
            elif kind == "reset":
                # Model emitted text before its tool call (rare): restart
                # parsing and tell the client to clear the panes.
                parser = TaggedStreamParser()
                yield _sse("reset", {})
            elif kind == "error":
                # Structured failure from the llm module — the client shows
                # a calm retry state; the draft in the DB is untouched.
                yield _sse("error", {"message": payload})
                return
        for name, section, data in parser.close():
            if name == "section":
                yield _sse("section", {"section": section, "delta": data})
            elif name == "icd_codes":
                yield _sse("icd_codes", data)
        yield _sse("done", {})

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
