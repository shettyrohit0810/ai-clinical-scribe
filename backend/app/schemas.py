"""Pydantic request/response models.

Validation lives here (leverage the platform — no hand-rolled checks in
routers). Response models double as the API contract the frontend types
mirror.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    # Provider identity on every row (not just admin views): a provider's
    # own list always shows their own name here too, so this is one shape
    # for both audiences rather than an admin-only variant.
    provider_id: int
    provider_name: str


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


# ---- Phase 6: admin dashboard -------------------------------------------------


class ProviderOut(BaseModel):
    """Admin-facing user shape — includes is_active/created_at that the
    self-serve UserOut (used by /auth/me) deliberately omits."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class ProviderCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        # Matches the exact normalization login() queries against
        # (User.email == body.email.lower().strip()) — a provider created
        # with mixed-case email must still be able to log in.
        return v.strip().lower()


class ProviderStatusUpdate(BaseModel):
    is_active: bool


class TemplateAdminOut(BaseModel):
    """Full row, including `instructions` and inactive templates — the
    provider-facing TemplateOut deliberately hides both."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    instructions: str
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    instructions: str = Field(min_length=1)


class TemplateUpdate(BaseModel):
    """Partial update — same model_fields_set pattern as EncounterPatch:
    only fields present in the request body are applied."""

    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    is_active: bool | None = None


class AuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    user_name: str
    action: str
    entity_type: str | None
    entity_id: int | None
    created_at: datetime
