"""Pydantic request/response models.

Validation lives here (leverage the platform — no hand-rolled checks in
routers). Response models double as the API contract the frontend types
mirror.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import EncounterStatus, UserRole

# ---- auth -------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: UserRole


# ---- patients / encounters ---------------------------------------------------


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    dob: date


class EncounterSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient: PatientOut
    status: EncounterStatus
    created_at: datetime
    updated_at: datetime


class EncounterOut(EncounterSummary):
    transcript: str


# ---- Phase 2: scribe workflow -------------------------------------------------


class IcdCodeItem(BaseModel):
    code: str
    description: str = ""


class DraftNote(BaseModel):
    """Unsaved workspace state, autosaved onto the encounter row."""

    subjective: str = ""
    objective: str = ""
    assessment: str = ""
    plan: str = ""
    icd_codes: list[IcdCodeItem] = []


class EncounterCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    dob: date
    template_id: int | None = None


class EncounterCreated(BaseModel):
    encounter_id: int
    patient: PatientOut
    # Returning-patient signal for the workspace banner: matched via the
    # composite identity (first, last, dob) — see models.Patient.
    returning: bool
    prior_encounters: int


class EncounterPatch(BaseModel):
    """Debounced autosave payload; every field optional so the client sends
    only what changed."""

    transcript: str | None = None
    template_id: int | None = None
    draft_note: DraftNote | None = None


class NoteVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_number: int
    subjective: str
    objective: str
    assessment: str
    plan: str
    icd_codes: list
    saved_by: int
    saved_at: datetime


class NoteVersionSummary(BaseModel):
    """List-view row for the version history panel — no note body, so
    listing versions never pulls four Text columns per row across the wire."""

    model_config = ConfigDict(from_attributes=True)

    version_number: int
    saved_by: int
    saved_by_name: str
    saved_at: datetime


class EncounterDetail(EncounterOut):
    template_id: int | None
    draft_note: DraftNote | None
    latest_version: NoteVersionOut | None


class NoteSaveRequest(BaseModel):
    subjective: str = ""
    objective: str = ""
    assessment: str = ""
    plan: str = ""
    icd_codes: list[IcdCodeItem] = []


class NoteSaveResponse(BaseModel):
    version_number: int
    saved_at: datetime


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
