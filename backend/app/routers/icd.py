"""ICD-10 semantic search widget: embed query -> Python cosine -> top 5.

Distinct from the internal candidate-constrained retrieval used during note
generation (app/icd.py:rank_candidates, called from routers/generation.py):
this endpoint is a direct, ad-hoc search the provider drives by typing —
e.g. to add a code the generated note didn't surface. Same local
hashed-BoW + cosine implementation; no new search infrastructure.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.icd import rank_candidates
from app.models import User
from app.schemas import IcdCodeItem

router = APIRouter(prefix="/icd", tags=["icd"])


@router.get("/search", response_model=list[IcdCodeItem])
def search_icd(
    q: str = Query(min_length=2, max_length=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    results = rank_candidates(db, q, k=5)
    return [IcdCodeItem(code=r.code, description=r.description) for r in results]
