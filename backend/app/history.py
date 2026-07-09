"""Patient history for the fetch_patient_history tool.

This is the server-side implementation the tool call executes: the model
never sees database ids and cannot ask for another patient — the backend
scopes the query to the encounter's patient, full stop. That containment is
the reason the tool takes no arguments.

Token discipline: newest 3 saved encounters, key sections truncated. History
should inform the note, not crowd out the transcript.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Encounter, EncounterStatus, NoteVersion, User

MAX_ENCOUNTERS = 3
MAX_SECTION_CHARS = 400


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_SECTION_CHARS:
        return text
    return text[:MAX_SECTION_CHARS].rsplit(" ", 1)[0] + " …"


def count_prior_saved(db: Session, encounter: Encounter) -> int:
    """Prior SAVED encounters for this patient (drafts have no signed note
    to reference; the current encounter is excluded)."""
    from sqlalchemy import func

    return db.scalar(
        select(func.count())
        .select_from(Encounter)
        .where(
            Encounter.patient_id == encounter.patient_id,
            Encounter.id != encounter.id,
            Encounter.status == EncounterStatus.saved,
        )
    )


def build_history_block(db: Session, encounter: Encounter) -> str:
    """Format prior saved notes as the fetch_patient_history tool result."""
    priors = db.scalars(
        select(Encounter)
        .where(
            Encounter.patient_id == encounter.patient_id,
            Encounter.id != encounter.id,
            Encounter.status == EncounterStatus.saved,
        )
        .order_by(Encounter.created_at.desc())
        .limit(MAX_ENCOUNTERS)
    ).all()

    if not priors:
        return "No prior encounters on record for this patient."

    blocks: list[str] = []
    for prior in priors:
        version = db.scalar(
            select(NoteVersion)
            .where(NoteVersion.encounter_id == prior.id)
            .order_by(NoteVersion.version_number.desc())
            .limit(1)
        )
        if version is None:
            continue
        provider = db.get(User, prior.provider_id)
        blocks.append(
            f"--- Encounter on {prior.created_at.date().isoformat()}"
            f" ({provider.full_name if provider else 'unknown provider'}) ---\n"
            f"Subjective: {_truncate(version.subjective)}\n"
            f"Assessment: {_truncate(version.assessment)}\n"
            f"Plan: {_truncate(version.plan)}"
        )
    return "\n\n".join(blocks) if blocks else "No prior encounters on record for this patient."
