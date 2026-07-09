"""Encounter routes. Provider data isolation is enforced HERE, server-side:

- List/read queries filter by provider_id taken from the authenticated token's
  user — never from a client-supplied parameter.
- A provider requesting another provider's encounter gets 404, not 403:
  returning 403 would confirm the id exists, which is itself a leak.
- Admins (dashboard, Phase 6) see all encounters.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import Encounter, User, UserRole
from app.schemas import EncounterOut, EncounterSummary

router = APIRouter(prefix="/encounters", tags=["encounters"])


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


@router.get("/{encounter_id}", response_model=EncounterOut)
def get_encounter(
    encounter_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    encounter = db.get(Encounter, encounter_id)
    if encounter is None or (
        user.role != UserRole.admin and encounter.provider_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Encounter not found")
    return encounter
