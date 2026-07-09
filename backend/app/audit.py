"""Audit log writer.

One helper, called inline wherever an auditable action happens (login, note
save, template/provider admin actions). It adds the row to the caller's
session and lets the caller commit — so the audit entry and the action it
records are one atomic transaction (an action can never commit without its
audit row, and vice versa).
"""

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit(
    db: Session,
    *,
    user_id: int,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    )
