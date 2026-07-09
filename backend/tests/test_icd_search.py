"""ICD search widget endpoint + catalog integrity.

Deterministic and LLM-free: search is local embed+cosine, no vendor call
exists on this path at all (not even a mocked one).
"""

from app.icd import embed_text
from app.models import IcdCode
from app.seed_icd import ICD_CODES
from tests.conftest import login

SAMPLE_CATALOG = [
    ("M25.561", "Pain in right knee"),
    ("M25.562", "Pain in left knee"),
    ("M25.569", "Pain in unspecified knee"),
    ("M17.11", "Unilateral primary osteoarthritis, right knee"),
    ("M25.511", "Pain in right shoulder"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("I10", "Essential (primary) hypertension"),
    ("Z23", "Encounter for immunization"),
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("F41.1", "Generalized anxiety disorder"),
]


def _seed_sample(db):
    for code, desc in SAMPLE_CATALOG:
        db.add(IcdCode(code=code, description=desc, embedding=embed_text(desc)))
    db.commit()


def test_knee_pain_query_returns_knee_family_codes(client, users, db):
    _seed_sample(db)
    login(client, "a@test.example")

    response = client.get("/api/icd/search", params={"q": "knee pain"})
    assert response.status_code == 200
    results = response.json()

    assert len(results) <= 5
    codes = {r["code"] for r in results}
    # The spec's exact scenario: "knee pain" must surface the M25.56x family.
    assert codes & {"M25.561", "M25.562", "M25.569"}
    assert "Z23" not in codes  # immunization shares no tokens — must lose
    for r in results:
        assert set(r.keys()) == {"code", "description"}


def test_search_requires_auth(client, users, db):
    _seed_sample(db)
    assert client.get("/api/icd/search", params={"q": "knee pain"}).status_code == 401


def test_search_query_too_short_is_rejected(client, users, db):
    login(client, "a@test.example")
    response = client.get("/api/icd/search", params={"q": "a"})
    assert response.status_code == 422


def test_search_is_capped_at_five_results(client, users, db):
    # More than 5 relevant candidates in the catalog; response must still cap at 5.
    for i in range(8):
        desc = f"Pain in right knee variant {i}"
        db.add(IcdCode(code=f"M25.5{i}9", description=desc, embedding=embed_text(desc)))
    db.commit()
    login(client, "a@test.example")

    results = client.get("/api/icd/search", params={"q": "knee pain"}).json()
    assert len(results) == 5


# ---- catalog integrity (Phase 5 deliverable: 250-300 real codes) -------------


def test_catalog_size_is_in_target_range():
    assert 250 <= len(ICD_CODES) <= 300


def test_catalog_has_no_duplicate_codes():
    codes = [c for c, _ in ICD_CODES]
    assert len(codes) == len(set(codes))


def test_catalog_codes_and_descriptions_are_nonempty():
    for code, description in ICD_CODES:
        assert code.strip() == code and code != ""
        assert description.strip() == description and description != ""
