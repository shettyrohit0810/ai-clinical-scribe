"""ICD candidate retrieval relevance."""

from app.icd import cosine, embed_text, rank_candidates
from app.models import IcdCode

CATALOG = [
    ("M25.561", "Pain in right knee"),
    ("M25.562", "Pain in left knee"),
    ("M17.11", "Unilateral primary osteoarthritis, right knee"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("I10", "Essential (primary) hypertension"),
    ("Z23", "Encounter for immunization"),
]


def test_embedding_is_deterministic_and_normalized():
    a = embed_text("Pain in right knee")
    assert a == embed_text("Pain in right knee")
    assert abs(cosine(a, a) - 1.0) < 1e-9


def test_knee_pain_ranks_knee_codes_first(db):
    for code, desc in CATALOG:
        db.add(IcdCode(code=code, description=desc, embedding=embed_text(desc)))
    db.commit()

    top = rank_candidates(db, "patient reports knee pain after running", k=3)
    top_codes = {r.code for r in top}
    assert "M25.561" in top_codes or "M25.562" in top_codes
    assert "Z23" not in top_codes  # immunization shares no tokens — must lose
