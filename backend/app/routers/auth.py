"""Auth routes: login, logout, me."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.auth import (
    clear_auth_cookie,
    create_access_token,
    get_current_user,
    set_auth_cookie,
    verify_password,
)
from app.db import get_db
from app.models import User
from app.schemas import LoginRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    # Identical 401 for unknown email and wrong password — no way to probe
    # which clinician emails exist (account-enumeration resistance).
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    set_auth_cookie(response, create_access_token(user))
    record_audit(db, user_id=user.id, action="login", entity_type="user", entity_id=user.id)
    db.commit()
    return user


@router.post("/logout")
def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    clear_auth_cookie(response)
    record_audit(db, user_id=user.id, action="logout", entity_type="user", entity_id=user.id)
    db.commit()
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """Session probe: the SPA calls this on load to restore the signed-in
    state from the httpOnly cookie (which JS itself cannot read)."""
    return user
