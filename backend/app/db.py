"""Database engine and request-scoped session dependency.

Connection pool settings — why each exists (walkthrough talking point):

- pool_size=10 — steady-state connections held open per worker process. We
  run 2 gunicorn workers, so the worst case is 2 x (10 + 5) = 30 connections:
  comfortably under a small RDS instance's max_connections (~100) while
  leaving headroom for Alembic runs and an operator psql session.
- max_overflow=5 — burst allowance: up to 5 extra connections may be opened
  under load and are closed when returned, so spikes queue briefly instead of
  failing while idle periods don't hold connections hostage.
- pool_pre_ping=True — each checkout first issues a liveness ping, so a
  connection silently dropped by an RDS failover/restart or idle timeout is
  replaced transparently instead of handing the first unlucky request a 500.

Without a pool, every request would pay a full TCP + TLS + Postgres auth
handshake to RDS (tens of ms) and a traffic spike could exhaust RDS
connection slots. No request code ever opens its own connection: everything
receives a session from the get_db() dependency below.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
