"""SQLAlchemy ORM models — the complete normalized schema.

All tables are created in one migration even though data arrives per phase
(ICD codes seed in Phase 5, templates in Phase 6): the data model was
designed and reviewed up front, so later phases add rows, not tables.

Every index and constraint carries its justification inline — these comments
are the ERD walkthrough answers.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    provider = "provider"
    admin = "admin"


class EncounterStatus(str, enum.Enum):
    draft = "draft"
    saved = "saved"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # UNIQUE doubles as the login-lookup index; no separate index needed.
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    # Deactivation flag rather than row deletion: encounters/audit rows keep
    # a valid FK and history stays intact (clinical data is never orphaned).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        # The composite UNIQUE *is* the returning-patient matcher per spec:
        # a new-encounter form with the same (first, last, dob) resolves to
        # this row instead of creating a duplicate patient.
        UniqueConstraint("first_name", "last_name", "dob", name="uq_patient_identity"),
        # Lookup index ordered last-name-first: clinical search convention is
        # "Thompson, Margaret" — supports prefix search on last name alone.
        Index("ix_patients_name_dob", "last_name", "first_name", "dob"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    dob: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    encounters: Mapped[list["Encounter"]] = relationship(back_populates="patient")


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Named `instructions`, deliberately not `system_prompt`: admin-authored
    # content is UNTRUSTED input. It is interpolated inside a fixed, quoted
    # frame in the user turn (see prompts.py, Phase 2) and can style the note
    # but can never override safety/faithfulness rules.
    instructions: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Encounter(Base):
    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    provider_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id"), nullable=True
    )
    transcript: Mapped[str] = mapped_column(Text, default="")
    # A draft IS an encounter row with status=draft — that single fact is the
    # session-persistence mechanism: DB-backed, so it survives refresh and
    # follows the provider across devices. No client-side draft storage.
    status: Mapped[EncounterStatus] = mapped_column(
        Enum(EncounterStatus, name="encounter_status"),
        default=EncounterStatus.draft,
    )
    # Scratch space for UNSAVED note edits ({subjective, objective,
    # assessment, plan, icd_codes}), autosaved alongside the transcript.
    # Deliberately separate from note_versions: this column is mutable
    # workspace state, versions are the immutable record. Cleared on save.
    draft_note: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped[Patient] = relationship(back_populates="encounters")
    note_versions: Mapped[list["NoteVersion"]] = relationship(
        back_populates="encounter", order_by="NoteVersion.version_number"
    )


# The provider dashboard's one hot query is "my encounters, newest first".
# Composite (provider_id, created_at DESC) serves both the filter and the
# sort in a single index scan. (A plain ASC index would also serve DESC via
# backward scan; declaring DESC matches the query shape for readability.)
Index(
    "ix_encounters_provider_created",
    Encounter.provider_id,
    Encounter.created_at.desc(),
)


class NoteVersion(Base):
    __tablename__ = "note_versions"
    __table_args__ = (
        # APPEND-ONLY invariant: rows are inserted, never updated or deleted.
        # The UNIQUE constraint (a) makes concurrent double-saves impossible
        # at the DB level and (b) its underlying btree on
        # (encounter_id, version_number) IS the history-retrieval index —
        # a second explicit index on the same columns would be redundant.
        UniqueConstraint(
            "encounter_id", "version_number", name="uq_note_version_per_encounter"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("encounters.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    subjective: Mapped[str] = mapped_column(Text, default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    assessment: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[str] = mapped_column(Text, default="")
    # List of {"code": "M25.561", "description": "..."} objects. JSONB rather
    # than a join table: codes are a snapshot OF THIS VERSION of the note
    # (append-only history must not change retroactively if the icd_codes
    # catalog is edited later).
    icd_codes: Mapped[list] = mapped_column(JSONB, default=list)
    saved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    encounter: Mapped[Encounter] = relationship(back_populates="note_versions")


class IcdCode(Base):
    __tablename__ = "icd_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    description: Mapped[str] = mapped_column(Text)
    # Embedding stored as a JSONB float array; similarity is computed with
    # Python cosine over ~250-300 rows (microseconds at this scale). pgvector
    # would add an extension + index type to defend for zero measurable win —
    # premature at this cardinality. Revisit at ~10k+ codes.
    embedding: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        # Audit views are time-ordered ("what happened recently") — created_at
        # is the only access path the UI uses.
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
