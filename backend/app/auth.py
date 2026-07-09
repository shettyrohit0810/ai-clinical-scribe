"""Security primitives: bcrypt hashing, JWT create/decode, auth dependencies.

This module owns every security decision; routers never touch jwt/bcrypt
directly. Design points (walkthrough material):

- JWT lives in an httpOnly cookie — invisible to JavaScript, so XSS can't
  exfiltrate it. SameSite=Lax blocks cross-site POSTs (CSRF) while keeping
  normal navigation working. `secure` is on in production (HTTPS-only).
- The token is stateless, but get_current_user re-reads the user row on every
  request and checks is_active — so an admin deactivating a provider takes
  effect on that provider's very next request, not at token expiry.
- The cookie's max_age (8h) deliberately outlives the token (30min): when the
  token expires the server still receives it and can answer "session
  expired" (distinct from "not logged in"), which powers the graceful
  re-auth-and-retry flow in Phase 9.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, WebSocket
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User, UserRole

ACCESS_TOKEN_COOKIE = "access_token"
COOKIE_MAX_AGE_SECONDS = 8 * 60 * 60  # outlives the JWT on purpose, see above

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user: User, expires_minutes: int | None = None) -> str:
    """expires_minutes override exists for tests (e.g. minting an already-
    expired token); production always uses the configured 30 minutes."""
    settings = get_settings()
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=get_settings().app_env == "production",
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        # Distinct message from "Not authenticated": the client shows the
        # re-login modal and retries the failed call (Phase 9, zero data loss).
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session")

    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    if not user.is_active:
        # Fresh DB check on every request: deactivation is immediate even
        # though the JWT itself is still cryptographically valid.
        raise HTTPException(status_code=403, detail="Account deactivated")
    return user


def get_current_user_ws(websocket: WebSocket, db: Session) -> User | None:
    """WebSocket counterpart to get_current_user (Phase 8: voice edit).

    A separate function rather than a shared generalization of
    get_current_user: the two transports fail differently on purpose. An
    HTTP request signals an auth failure by raising HTTPException, which
    FastAPI turns into a 401/403 response. A WebSocket route must instead
    explicitly `await websocket.close(code=...)` — there is no response
    object to attach a status to — so this returns None on any failure and
    lets the caller decide the close code, rather than raising.
    """
    token = websocket.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM]
        )
    except jwt.InvalidTokenError:
        return None
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        return None
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    # Authorization uses the DB row's role, not the token claim: the claim is
    # a convenience for the client; the database is the authority.
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
