"""Conversational voice editing over WebSocket (Phase 8).

Mounted under /ws (not /api) — nginx and the dev Vite proxy route that
prefix to a WebSocket upgrade, kept separate from the plain-HTTP /api
location so the SSE-tuning proxy settings on /api never have to account for
a persistent connection type they weren't written for.

Wire protocol — one command in, one JSON patch applied and echoed back:

    client -> server   {"type": "command", "text": "<one recognized voice command>"}
    server -> client    {"type": "patch_applied", "note": {...}, "patch": {...}, "message": "..."}
                        {"type": "error", "message": "..."}

The model NEVER writes note content directly: every command is turned into
a JSON patch by a one-shot completion (app/llm.complete_json, sonnet tier —
see llm.py's own tier-rationale docstring, settled since Phase 2), and the
ONLY thing allowed to turn that patch into new note content is
app.note_patch.apply_note_patch. Malformed client messages, malformed model
output, and patches that fail validation (unknown op, text that doesn't
match verbatim, etc.) all resolve to a graceful {"type": "error", ...} reply
on the same connection — the loop keeps running and waits for the next
command; nothing is ever partially applied.
"""

import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app import llm
from app.audit import record_audit
from app.auth import get_current_user_ws
from app.db import get_db
from app.models import Encounter, UserRole
from app.note_patch import InvalidPatchError, apply_note_patch
from app.prompts import VOICE_EDIT_SYSTEM, build_voice_edit_user_prompt
from app.stream_parser import SOAP_SECTIONS

logger = logging.getLogger("app.voice_edit")

router = APIRouter(tags=["voice_edit"])

_SUMMARY_TEMPLATES = {
    "add": "Added to {section}.",
    "remove": "Removed from {section}.",
    "rewrite": "Rewrote {section}.",
    "move": "Moved to {to_section}.",
}


def _current_note(encounter: Encounter) -> tuple[dict[str, str], list]:
    """Same draft-wins-over-saved precedence as EncounterDetail. Returns
    (soap_sections, icd_codes) — icd_codes is carried through untouched by
    every voice-edit patch (voice edits are scoped to the four text
    sections only) so a patch can never silently drop the ICD chips."""
    if encounter.draft_note:
        note = {s: encounter.draft_note.get(s, "") for s in SOAP_SECTIONS}
        return note, encounter.draft_note.get("icd_codes", [])
    if encounter.note_versions:
        latest = encounter.note_versions[-1]
        return {s: getattr(latest, s) for s in SOAP_SECTIONS}, latest.icd_codes
    return {s: "" for s in SOAP_SECTIONS}, []


def _summarize(patch: dict) -> str:
    template = _SUMMARY_TEMPLATES.get(patch.get("op"), "Note updated.")
    return template.format(
        section=patch.get("section", ""), to_section=patch.get("to_section", "")
    )


def _strip_code_fence(text: str) -> str:
    """The prompt says "no markdown fences"; defend against the model doing
    it anyway rather than failing every such response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


@router.websocket("/encounters/{encounter_id}/voice-edit")
async def voice_edit(
    websocket: WebSocket,
    encounter_id: int,
    db: Session = Depends(get_db),
):
    user = get_current_user_ws(websocket, db)
    if user is None:
        await websocket.close(code=4401)
        return

    encounter = db.get(Encounter, encounter_id)
    if encounter is None or (
        user.role != UserRole.admin and encounter.provider_id != user.id
    ):
        # Same 404-not-403 spirit as the REST isolation rule (existence must
        # not leak) — closest WS equivalent is just closing without detail.
        await websocket.close(code=4404)
        return

    await websocket.accept()

    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            msg = json.loads(raw)
            command_text = msg["text"].strip()
            if msg.get("type") != "command" or not command_text:
                raise ValueError("empty or wrong-type command")
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError):
            await websocket.send_json({"type": "error", "message": "Malformed command."})
            continue

        # Fresh read before every command: draft_note may have changed via
        # the REST autosave path (e.g. a manual pane edit) between voice
        # commands in the same long-lived connection.
        db.refresh(encounter)
        current_note, icd_codes = _current_note(encounter)
        user_prompt = build_voice_edit_user_prompt(note=current_note, command_text=command_text)

        kind, result = await llm.complete_json(
            model=llm.MODEL_FINAL,
            system=VOICE_EDIT_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=llm.MAX_PATCH_TOKENS,
        )
        if kind == "error":
            await websocket.send_json({"type": "error", "message": result})
            continue

        try:
            patch = json.loads(_strip_code_fence(result))
        except json.JSONDecodeError:
            await websocket.send_json(
                {"type": "error", "message": "Could not understand that as an edit."}
            )
            continue

        if isinstance(patch, dict) and patch.get("op") == "unclear":
            await websocket.send_json(
                {"type": "error", "message": "Didn't catch that as an edit — try again."}
            )
            continue

        try:
            new_note = apply_note_patch(current_note, patch)
        except InvalidPatchError as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            continue

        encounter.draft_note = {**new_note, "icd_codes": icd_codes}
        record_audit(
            db, user_id=user.id, action="voice_edit_patch",
            entity_type="encounter", entity_id=encounter.id,
        )
        db.commit()
        logger.info(
            "voice_edit_patch encounter=%d op=%s", encounter.id, patch.get("op")
        )

        await websocket.send_json({
            "type": "patch_applied",
            "note": new_note,
            "patch": patch,
            "message": _summarize(patch),
        })
