"""FastAPI application entrypoint.

Every route is mounted under /api so nginx routing stays a single location
block and the SPA fallback (`try_files ... /index.html`) can never shadow an
API path.
"""

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.dev import router as dev_router

app = FastAPI(title="AI Clinical Scribe API")

api = APIRouter(prefix="/api")


@api.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    """Liveness + DB reachability. Used by the deploy runbook's verify steps.

    Reports the DB as a field rather than failing the endpoint: a down
    database shouldn't make the app itself look dead to a health probe.
    """
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
    return {"status": "ok", "database": database}


api.include_router(dev_router)
app.include_router(api)
