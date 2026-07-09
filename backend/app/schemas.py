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
