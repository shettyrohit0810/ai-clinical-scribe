"""FastAPI application entrypoint.

Every route is mounted under /api so nginx routing stays a single location
block and the SPA fallback (`try_files ... /index.html`) can never shadow an
API path.
"""

import logging

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

# Surface app.* INFO logs (LLM call accounting, tool invocations) alongside
# uvicorn's access log — these lines are demo evidence (e.g. the server-side
# fetch_patient_history execution). basicConfig is a no-op if a root handler
# already exists, so this can't double-log under gunicorn.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

from app.db import get_db
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.dev import router as dev_router
from app.routers.encounters import router as encounters_router
from app.routers.generation import router as generation_router
from app.routers.icd import router as icd_router
from app.routers.templates import router as templates_router
from app.routers.voice_edit import router as voice_edit_router

app = FastAPI(title="AI Clinical Scribe API")

api = APIRouter(prefix="/api")
# Voice-edit is a WebSocket route, kept off /api on purpose: nginx and the
# dev Vite proxy each have a SEPARATE /ws location with the Upgrade/
# Connection headers a WebSocket handshake needs, reserved for this since
# Phase 0 (see infra/nginx/ai-scribe.conf) rather than bolted onto the
# SSE-tuned /api location.
ws = APIRouter(prefix="/ws")


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


api.include_router(auth_router)
api.include_router(admin_router)
api.include_router(encounters_router)
api.include_router(generation_router)
api.include_router(icd_router)
api.include_router(templates_router)
api.include_router(dev_router)
app.include_router(api)

ws.include_router(voice_edit_router)
app.include_router(ws)
