"""Pure patch-application engine for voice-edited SOAP notes.

apply_note_patch is the ONLY function in the codebase allowed to mutate note
section content in response to a voice command — routers/voice_edit.py never
writes model output directly into a note; it always goes through here.

Pure and I/O-free by design (same rationale as stream_parser.py): fully
unit-testable without a running LLM, and deterministic. The remove/move
operations are hallucination-resistant by construction — the model must
supply an exact, verbatim substring of the CURRENT section content, so there
is no path for the model to silently rewrite content it wasn't asked to
touch. Rewrite is the only operation allowed to replace a section wholesale;
add/remove/move only ever touch the specific text named in the patch, and
every operation leaves every OTHER section byte-identical to the input.
"""

import re

from app.stream_parser import SOAP_SECTIONS

VALID_OPS = ("add", "remove", "rewrite", "move")

_MULTI_SPACE = re.compile(r" {2,}")
_SPACE_BEFORE_PUNCT = re.compile(r" +([.,;:!?])")


class InvalidPatchError(Exception):
    """A patch failed validation: malformed shape, unknown op, unknown
    section, missing/empty text, or (for remove/move) text that isn't an
    exact substring of the section it claims to modify. Callers treat this
    as a graceful no-op — nothing is mutated, the message is relayed to the
    user, and the caller waits for the next command."""


def apply_note_patch(note: dict[str, str], patch: dict) -> dict[str, str]:
    """Apply one validated patch to `note` and return a NEW dict. `note` is
    never mutated in place, and every section the patch doesn't target is
    copied over unchanged — the content-preservation invariant."""
    if not isinstance(patch, dict):
        raise InvalidPatchError("Patch must be a JSON object.")

    op = patch.get("op")
    if op not in VALID_OPS:
        raise InvalidPatchError(f"Unrecognized operation: {op!r}")

    result = dict(note)

    if op == "move":
        from_section = patch.get("from_section")
        to_section = patch.get("to_section")
        text = patch.get("text")
        _require_section(from_section)
        _require_section(to_section)
        _require_text(text)
        if from_section == to_section:
            raise InvalidPatchError("move requires two different sections.")
        result[from_section] = _remove_substring(result[from_section], text, from_section)
        result[to_section] = _append(result[to_section], text)
        return result

    section = patch.get("section")
    text = patch.get("text")
    _require_section(section)
    _require_text(text)

    if op == "add":
        result[section] = _append(result[section], text)
    elif op == "remove":
        result[section] = _remove_substring(result[section], text, section)
    elif op == "rewrite":
        result[section] = text.strip()

    return result


def _require_section(section: object) -> None:
    if section not in SOAP_SECTIONS:
        raise InvalidPatchError(f"Unknown section: {section!r}")


def _require_text(text: object) -> None:
    if not isinstance(text, str) or not text.strip():
        raise InvalidPatchError("Patch is missing non-empty text.")


def _append(existing: str, addition: str) -> str:
    existing = existing.strip()
    addition = addition.strip()
    if not existing:
        return addition
    return f"{existing} {addition}"


def _remove_substring(existing: str, text: str, section: str) -> str:
    if text not in existing:
        raise InvalidPatchError(
            f"Text to remove was not found verbatim in {section}."
        )
    updated = existing.replace(text, "", 1)
    # Clean up only the two artifact shapes a literal excision can leave —
    # a doubled space, or a space stranded before punctuation ("pain ." ->
    # "pain.") — deliberately NOT touching newlines, so a multi-line PLAN
    # (e.g. a numbered list) elsewhere in the same section keeps its
    # formatting untouched by an edit somewhere else in the text.
    updated = _MULTI_SPACE.sub(" ", updated)
    updated = _SPACE_BEFORE_PUNCT.sub(r"\1", updated).strip()
    return updated
