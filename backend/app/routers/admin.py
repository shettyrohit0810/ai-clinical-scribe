"""Admin dashboard: provider management, template CRUD, audit log view.

Everything here is new admin-only surface — nothing here duplicates
provider or encounter logic:

- Provider create/deactivate reuses `hash_password` and the `User` model
  as-is; no parallel auth path.
- All-encounters filtering was NOT re-implemented here — it's an extension
  of the existing `GET /api/encounters` in routers/encounters.py.
- Template freshness is untouched: generation.py already reads a template's
  `instructions` fresh from the DB by id at generation time. Editing a row
  here is exactly the write side of that existing read-at-generation
  design — no cache to invalidate, no new plumbing required.
- Every mutation writes an audit_log row via the same `record_audit` helper
  every other admin/provider action already uses.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.auth import hash_password, require_admin
from app.db import get_db
from app.models import AuditLog, Template, User, UserRole
from app.schemas import (
    AuditLogEntryOut,
    ProviderCreate,
    ProviderOut,
    ProviderStatusUpdate,
    TemplateAdminOut,
    TemplateCreate,
    TemplateUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---- providers ----------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    return db.scalars(
        select(User).where(User.role == UserRole.provider).order_by(User.full_name)
    ).all()


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(
    body: ProviderCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = User(
        email=body.email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=UserRole.provider,
    )
    db.add(provider)
    try:
        db.flush()
    except IntegrityError:
        # users.email UNIQUE — a real DB constraint, not just app-level
        # validation, so this is a genuine race guard, not decoration.
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already in use")
    record_audit(
        db, user_id=admin.id, action="provider_create",
        entity_type="user", entity_id=provider.id,
    )
    db.commit()
    return provider


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider_status(
    provider_id: int,
    body: ProviderStatusUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = db.get(User, provider_id)
    if provider is None or provider.role != UserRole.provider:
        # Scoped to role=provider only, which also rules out admin
        # self-lockout by construction: an admin's own row has
        # role=admin, so it can never match here and this route can never
        # touch it — no separate self-check needed.
        raise HTTPException(status_code=404, detail="Provider not found")

    provider.is_active = body.is_active
    record_audit(
        db,
        user_id=admin.id,
        action="provider_activate" if body.is_active else "provider_deactivate",
        entity_type="user",
        entity_id=provider.id,
    )
    db.commit()
    return provider


# ---- templates ------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateAdminOut])
def list_all_templates(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Unlike GET /api/templates (active-only, no instructions), this
    surfaces every template including inactive ones and their full
    instructions — the admin's editing view."""
    return db.scalars(select(Template).order_by(Template.name)).all()


@router.post("/templates", response_model=TemplateAdminOut, status_code=201)
def create_template(
    body: TemplateCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    template = Template(
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        created_by=admin.id,
    )
    db.add(template)
    db.flush()
    record_audit(
        db, user_id=admin.id, action="template_create",
        entity_type="template", entity_id=template.id,
    )
    db.commit()
    return template


@router.patch("/templates/{template_id}", response_model=TemplateAdminOut)
def update_template(
    template_id: int,
    body: TemplateUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Partial update — no DELETE endpoint. `is_active=false` is the soft
    delete: encounters.template_id keeps a valid FK for any historical
    encounter that referenced this template, and the read-at-generation
    check in generation.py already skips inactive templates."""
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    fields = body.model_fields_set
    if "name" in fields and body.name:
        template.name = body.name
    if "description" in fields:
        template.description = body.description or ""
    if "instructions" in fields and body.instructions:
        template.instructions = body.instructions
    if "is_active" in fields:
        template.is_active = body.is_active

    record_audit(
        db, user_id=admin.id, action="template_update",
        entity_type="template", entity_id=template.id,
    )
    db.commit()
    return template


# ---- audit log ------------------------------------------------------------


@router.get("/audit", response_model=list[AuditLogEntryOut])
def list_audit_log(
    limit: int = Query(100, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(AuditLog, User.full_name)
        .join(User, User.id == AuditLog.user_id)
        # ix_audit_log_created_at (models.py) backs this scan.
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return [
        AuditLogEntryOut(
            id=entry.id,
            user_id=entry.user_id,
            user_name=name,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            created_at=entry.created_at,
        )
        for entry, name in rows
    ]
