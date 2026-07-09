"""Encounter routes. Provider data isolation is enforced HERE, server-side:

- List/read queries filter by provider_id taken from the authenticated token's
  user — never from a client-supplied parameter.
- A provider requesting another provider's encounter gets 404, not 403:
  returning 403 would confirm the id exists, which is itself a leak.
- Admins (dashboard, Phase 6) see all encounters.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.audit import record_audit
from app.auth import get_current_user
from app.db import get_db
from app.models import (
    Encounter,
    EncounterStatus,
    NoteVersion,
    Patient,
    User,
    UserRole,
)
from app.schemas import (
    EncounterCreate,
    EncounterCreated,
    EncounterDetail,
    EncounterPatch,
    EncounterSummary,
    NoteSaveRequest,
    NoteSaveResponse,
    NoteVersionOut,
    NoteVersionSummary,
)

router = APIRouter(prefix="/encounters", tags=["encounters"])


def get_owned_encounter(
    encounter_id: int, user: User, db: Session
) -> Encounter:
    """Shared ownership gate for every by-id route."""
    encounter = db.get(Encounter, encounter_id)
    if encounter is None or (
        user.role != UserRole.admin and encounter.provider_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Encounter not found")
    return encounter


@router.get("", response_model=list[EncounterSummary])
def list_encounters(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    query = (
        select(Encounter)
        .options(joinedload(Encounter.patient))
        # Order matches ix_encounters_provider_created — single index scan.
        .order_by(Encounter.created_at.desc())
    )
    if user.role != UserRole.admin:
        query = query.where(Encounter.provider_id == user.id)
    return db.scalars(query).all()


@router.post("", response_model=EncounterCreated, status_code=201)
def create_encounter(
    body: EncounterCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    first = body.first_name.strip()
    last = body.last_name.strip()

    # Returning-patient matcher: the composite identity (first, last, dob).
    # Lookup is case-insensitive so "margaret thompson" finds the existing
    # row; the DB's composite UNIQUE remains the hard backstop for exact
    # duplicates racing in concurrently.
    patient = db.scalar(
        select(Patient).where(
            func.lower(Patient.first_name) == first.lower(),
            func.lower(Patient.last_name) == last.lower(),
            Patient.dob == body.dob,
        )
    )
    returning = patient is not None
    if patient is None:
        patient = Patient(first_name=first, last_name=last, dob=body.dob)
        db.add(patient)
        db.flush()

    prior = db.scalar(
        select(func.count())
        .select_from(Encounter)
        .where(Encounter.patient_id == patient.id)
    )

    encounter = Encounter(
        patient_id=patient.id,
        provider_id=user.id,  # from the token — never from the client
        template_id=body.template_id,
        status=EncounterStatus.draft,
    )
    db.add(encounter)
    db.flush()
    record_audit(
        db, user_id=user.id, action="encounter_create",
        entity_type="encounter", entity_id=encounter.id,
    )
    db.commit()
    return EncounterCreated(
        encounter_id=encounter.id,
        patient=patient,
        returning=returning,
        prior_encounters=prior,
    )


@router.get("/{encounter_id}", response_model=EncounterDetail)
def get_encounter(
    encounter_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    encounter = get_owned_encounter(encounter_id, user, db)
    latest = db.scalar(
        select(NoteVersion)
        .where(NoteVersion.encounter_id == encounter.id)
        .order_by(NoteVersion.version_number.desc())
        .limit(1)
    )
    return EncounterDetail(
        id=encounter.id,
        patient=encounter.patient,
        status=encounter.status,
        created_at=encounter.created_at,
        updated_at=encounter.updated_at,
        transcript=encounter.transcript,
        template_id=encounter.template_id,
        draft_note=encounter.draft_note,
        latest_version=latest,
    )


@router.patch("/{encounter_id}")
def patch_encounter(
    encounter_id: int,
    body: EncounterPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Debounced autosave target (~3s from the client). Persisting the
    transcript + unsaved edits onto the encounter row IS the session
    persistence mechanism: refresh the page or open another device and the
    draft is exactly where it was."""
    encounter = get_owned_encounter(encounter_id, user, db)

    # model_fields_set: only apply what the client actually sent, so a
    # transcript-only patch can't wipe a draft note (and vice versa).
    if "transcript" in body.model_fields_set:
        encounter.transcript = body.transcript or ""
    if "template_id" in body.model_fields_set:
        encounter.template_id = body.template_id
    if "draft_note" in body.model_fields_set:
        encounter.draft_note = (
            body.draft_note.model_dump() if body.draft_note else None
        )

    db.commit()
    return {"status": "ok", "updated_at": encounter.updated_at}


@router.post("/{encounter_id}/save", response_model=NoteSaveResponse)
def save_note(
    encounter_id: int,
    body: NoteSaveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Append-only save: every save inserts the next note_versions row.
    Nothing is ever updated or deleted; the unique constraint on
    (encounter_id, version_number) makes a concurrent double-save a DB
    error rather than silent data corruption."""
    encounter = get_owned_encounter(encounter_id, user, db)

    current_max = (
        db.scalar(
            select(func.max(NoteVersion.version_number)).where(
                NoteVersion.encounter_id == encounter.id
            )
        )
        or 0
    )
    version = NoteVersion(
        encounter_id=encounter.id,
        version_number=current_max + 1,
        subjective=body.subjective,
        objective=body.objective,
        assessment=body.assessment,
        plan=body.plan,
        icd_codes=[c.model_dump() for c in body.icd_codes],
        saved_by=user.id,
    )
    db.add(version)
    encounter.status = EncounterStatus.saved
    encounter.draft_note = None  # workspace scratch is now the saved record
    record_audit(
        db, user_id=user.id, action="note_save",
        entity_type="encounter", entity_id=encounter.id,
    )
    db.commit()
    return NoteSaveResponse(
        version_number=version.version_number, saved_at=version.saved_at
    )


@router.get("/{encounter_id}/versions", response_model=list[NoteVersionSummary])
def list_versions(
    encounter_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Version history panel data. Ordered oldest-first (matches the
    UNIQUE(encounter_id, version_number) btree — see models.py — so this is
    a single index-ordered scan, no extra index to justify)."""
    encounter = get_owned_encounter(encounter_id, user, db)
    rows = db.execute(
        select(NoteVersion, User.full_name)
        .join(User, User.id == NoteVersion.saved_by)
        .where(NoteVersion.encounter_id == encounter.id)
        .order_by(NoteVersion.version_number)
    ).all()
    return [
        NoteVersionSummary(
            version_number=v.version_number,
            saved_by=v.saved_by,
            saved_by_name=name,
            saved_at=v.saved_at,
        )
        for v, name in rows
    ]


@router.get(
    "/{encounter_id}/versions/{version_number}", response_model=NoteVersionOut
)
def get_version(
    encounter_id: int,
    version_number: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """View any single version, read fresh from RDS — the append-only table
    IS the history store, so there's no separate cache to go stale."""
    encounter = get_owned_encounter(encounter_id, user, db)
    version = db.scalar(
        select(NoteVersion).where(
            NoteVersion.encounter_id == encounter.id,
            NoteVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return version
