"""Seed the icd_codes table:  python -m app.seed_icd

Starter set of ~60 real ICD-10-CM codes across common outpatient
specialties — enough for every Phase 2/3 demo scenario. Phase 5 expands
this catalog toward 250-300 codes; the mechanism (upsert by code, embed at
seed time) is final now. Idempotent: re-running updates descriptions and
re-embeds, never duplicates.
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.icd import embed_text
from app.models import IcdCode

ICD_CODES: list[tuple[str, str]] = [
    # Musculoskeletal / orthopedic
    ("M25.511", "Pain in right shoulder"),
    ("M25.512", "Pain in left shoulder"),
    ("M25.561", "Pain in right knee"),
    ("M25.562", "Pain in left knee"),
    ("M25.569", "Pain in unspecified knee"),
    ("M17.11", "Unilateral primary osteoarthritis, right knee"),
    ("M17.12", "Unilateral primary osteoarthritis, left knee"),
    ("M17.0", "Bilateral primary osteoarthritis of knee"),
    ("M54.2", "Cervicalgia"),
    ("M54.50", "Low back pain, unspecified"),
    ("M62.830", "Muscle spasm of back"),
    ("M77.9", "Enthesopathy, unspecified"),
    ("S93.401A", "Sprain of unspecified ligament of right ankle, initial encounter"),
    ("S93.402A", "Sprain of unspecified ligament of left ankle, initial encounter"),
    ("S93.412A", "Sprain of calcaneofibular ligament of left ankle, initial encounter"),
    # Respiratory / ENT
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("J02.9", "Acute pharyngitis, unspecified"),
    ("J01.90", "Acute sinusitis, unspecified"),
    ("J20.9", "Acute bronchitis, unspecified"),
    ("J18.9", "Pneumonia, unspecified organism"),
    ("J45.909", "Unspecified asthma, uncomplicated"),
    ("J30.9", "Allergic rhinitis, unspecified"),
    ("H66.90", "Otitis media, unspecified, unspecified ear"),
    ("R05.1", "Acute cough"),
    ("R06.02", "Shortness of breath"),
    ("U07.1", "COVID-19"),
    ("J11.1", "Influenza due to unidentified influenza virus with other respiratory manifestations"),
    # Cardiovascular
    ("I10", "Essential (primary) hypertension"),
    ("I25.10", "Atherosclerotic heart disease of native coronary artery without angina pectoris"),
    ("I48.91", "Unspecified atrial fibrillation"),
    ("I50.9", "Heart failure, unspecified"),
    ("R07.9", "Chest pain, unspecified"),
    ("E78.5", "Hyperlipidemia, unspecified"),
    # Endocrine / metabolic
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("E11.65", "Type 2 diabetes mellitus with hyperglycemia"),
    ("E03.9", "Hypothyroidism, unspecified"),
    ("E66.9", "Obesity, unspecified"),
    ("E86.0", "Dehydration"),
    ("E55.9", "Vitamin D deficiency, unspecified"),
    # Gastrointestinal
    ("K21.9", "Gastro-esophageal reflux disease without esophagitis"),
    ("K59.00", "Constipation, unspecified"),
    ("K52.9", "Noninfective gastroenteritis and colitis, unspecified"),
    ("R10.9", "Unspecified abdominal pain"),
    ("R11.2", "Nausea with vomiting, unspecified"),
    # Genitourinary
    ("N30.00", "Acute cystitis without hematuria"),
    ("N39.0", "Urinary tract infection, site not specified"),
    ("N20.0", "Calculus of kidney"),
    # Neurology
    ("G43.909", "Migraine, unspecified, not intractable, without status migrainosus"),
    ("R51.9", "Headache, unspecified"),
    ("G47.00", "Insomnia, unspecified"),
    ("R42", "Dizziness and giddiness"),
    # Mental health
    ("F41.1", "Generalized anxiety disorder"),
    ("F32.A", "Depression, unspecified"),
    ("F43.23", "Adjustment disorder with mixed anxiety and depressed mood"),
    # Dermatology
    ("L03.90", "Cellulitis, unspecified"),
    ("L70.0", "Acne vulgaris"),
    ("L23.9", "Allergic contact dermatitis, unspecified cause"),
    # Eyes
    ("H10.9", "Unspecified conjunctivitis"),
    # General / preventive / symptoms
    ("Z00.00", "Encounter for general adult medical examination without abnormal findings"),
    ("Z00.01", "Encounter for general adult medical examination with abnormal findings"),
    ("Z23", "Encounter for immunization"),
    ("B34.9", "Viral infection, unspecified"),
    ("R50.9", "Fever, unspecified"),
    ("R53.83", "Other fatigue"),
]


def seed_icd() -> None:
    db = SessionLocal()
    try:
        existing = {
            row.code: row for row in db.scalars(select(IcdCode)).all()
        }
        created = updated = 0
        for code, description in ICD_CODES:
            embedding = embed_text(description)  # embed once, at seed time
            row = existing.get(code)
            if row is None:
                db.add(IcdCode(code=code, description=description, embedding=embedding))
                created += 1
            else:
                row.description = description
                row.embedding = embedding
                updated += 1
        db.commit()
        print(f"ICD codes: {created} created, {updated} refreshed ({len(ICD_CODES)} total).")
    finally:
        db.close()


if __name__ == "__main__":
    seed_icd()
