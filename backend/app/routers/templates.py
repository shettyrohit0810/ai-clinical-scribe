"""Template routes (provider-facing). Phase 6 adds admin CRUD.

The list endpoint exposes name/description only — `instructions` are
server-side prompt material, injected during generation (read fresh from
the DB each time). Providers pick a template by what it's for, not by its
prompt text.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Template, User
from app.schemas import TemplateOut

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut])
def list_templates(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return db.scalars(
        select(Template).where(Template.is_active.is_(True)).order_by(Template.name)
    ).all()
