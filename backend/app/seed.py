"""Idempotent demo seed data:  python -m app.seed

- 4 users (3 providers + 1 admin) — demo credentials, intentionally listed in
  the README; these are stage props, not secrets.
- 6 patients with plausible identities.
- 8 historical SAVED encounters with realistic transcripts and v1 SOAP notes,
  distributed so every demo in the script has data behind it:
    * Margaret Thompson has THREE prior encounters (two with Dr. Chen) —
      the returning-patient / history-injection demo (Phase 3).
    * Encounters span all three providers — the admin dashboard filters
      (Phase 6) and the provider-isolation demo look real.

Idempotency: keyed on user emails / patient identity — running twice adds
nothing (safe on a box that already has data).
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Encounter, EncounterStatus, NoteVersion, Patient, User, UserRole

DEMO_PASSWORD = "ScribeDemo1!"  # demo stage prop — listed in README on purpose

USERS = [
    ("sarah.chen@clinic.example", "Dr. Sarah Chen", UserRole.provider),
    ("james.patel@clinic.example", "Dr. James Patel", UserRole.provider),
    ("maria.okafor@clinic.example", "Dr. Maria Okafor", UserRole.provider),
    ("admin@clinic.example", "Alex Rivera (Admin)", UserRole.admin),
]

PATIENTS = [
    ("Margaret", "Thompson", date(1954, 3, 17)),
    ("David", "Kim", date(1988, 11, 2)),
    ("Rosa", "Alvarez", date(1979, 6, 25)),
    ("Ethan", "Wright", date(2001, 9, 8)),
    ("Priya", "Sharma", date(1965, 1, 30)),
    ("Samuel", "Osei", date(1992, 4, 12)),
]

# (provider_email, patient_index, days_ago, transcript, S, O, A, P, icd_codes)
ENCOUNTERS = [
    (
        "sarah.chen@clinic.example", 0, 180,
        "Margaret Thompson, 71, here for right knee pain, worse over three months. "
        "Pain with stairs and after sitting. No injury. Tried acetaminophen with "
        "partial relief. Exam: mild effusion right knee, crepitus, medial joint "
        "line tenderness, ROM 0 to 115 degrees. Neurovascularly intact.",
        "71-year-old female presents with 3 months of progressive right knee pain, "
        "worse with stairs and prolonged sitting. No trauma. Partial relief with "
        "acetaminophen.",
        "Right knee: mild effusion, crepitus with motion, medial joint line "
        "tenderness. ROM 0-115 degrees. Distal neurovascular exam intact.",
        "Right knee osteoarthritis, symptomatic. (M17.11 — Unilateral primary "
        "osteoarthritis, right knee)",
        "1. Weight-bearing knee X-ray. 2. Start naproxen 500 mg BID with food. "
        "3. Physical therapy referral for quadriceps strengthening. 4. Follow up "
        "in 6 weeks.",
        [{"code": "M17.11", "description": "Unilateral primary osteoarthritis, right knee"}],
    ),
    (
        "sarah.chen@clinic.example", 0, 60,
        "Margaret Thompson follow-up for right knee osteoarthritis. X-ray showed "
        "moderate medial compartment narrowing. PT helping somewhat. Pain now 4 "
        "out of 10 from 7. Naproxen tolerated, no GI symptoms. Exam: effusion "
        "resolved, still crepitus, gait improved.",
        "Follow-up for right knee OA. Reports pain improved from 7/10 to 4/10 "
        "with PT and naproxen. No GI upset. Adherent to home exercise program.",
        "Right knee: no effusion today, crepitus persists, gait improved. X-ray "
        "reviewed: moderate medial compartment joint space narrowing.",
        "Right knee osteoarthritis, improving on conservative management. "
        "(M17.11)",
        "1. Continue PT 4 more weeks and home exercises. 2. Continue naproxen "
        "PRN, taper as tolerated. 3. Discussed intra-articular steroid injection "
        "if plateau. 4. Return in 3 months or sooner if worse.",
        [{"code": "M17.11", "description": "Unilateral primary osteoarthritis, right knee"}],
    ),
    (
        "james.patel@clinic.example", 0, 21,
        "Mrs. Thompson seen urgently for two days of burning with urination and "
        "frequency. No fever, no flank pain, no nausea. Urine dip in office: "
        "positive leukocyte esterase and nitrites.",
        "71-year-old female with 2 days of dysuria and urinary frequency. Denies "
        "fever, flank pain, nausea, or hematuria.",
        "Afebrile, abdomen soft, no suprapubic or CVA tenderness. Urine dipstick: "
        "LE positive, nitrites positive.",
        "Acute uncomplicated cystitis. (N30.00 — Acute cystitis without "
        "hematuria)",
        "1. Nitrofurantoin 100 mg BID x 5 days. 2. Urine culture sent. "
        "3. Increase fluids. 4. Return precautions for fever or flank pain "
        "reviewed.",
        [{"code": "N30.00", "description": "Acute cystitis without hematuria"}],
    ),
    (
        "sarah.chen@clinic.example", 1, 45,
        "David Kim, 37, annual visit. Feels well. Runs three times weekly. No "
        "medications. Family history of type 2 diabetes in father. BP 118 over "
        "76, BMI 24. Labs ordered.",
        "37-year-old male for annual preventive visit. Asymptomatic, exercises "
        "regularly, no medications. Family history: T2DM (father).",
        "BP 118/76, HR 62, BMI 24. Cardiopulmonary exam normal. No edema.",
        "Healthy adult male, routine health maintenance. (Z00.00 — General adult "
        "medical examination without abnormal findings)",
        "1. Fasting lipid panel and HbA1c given family history. 2. Continue "
        "current exercise. 3. Return in 1 year or with results if abnormal.",
        [{"code": "Z00.00", "description": "Encounter for general adult medical examination without abnormal findings"}],
    ),
    (
        "james.patel@clinic.example", 2, 90,
        "Rosa Alvarez, 46, follow-up hypertension. Home readings average 148 over "
        "92 despite lisinopril 10. Taking it daily. No chest pain, no headaches. "
        "Exam BP 150 over 94, otherwise normal.",
        "46-year-old female, hypertension follow-up. Home BP log averages 148/92 "
        "on lisinopril 10 mg daily with good adherence. Asymptomatic.",
        "BP 150/94 confirmed on repeat. HR 74. Cardiopulmonary exam normal, no "
        "edema, fundi not examined.",
        "Essential hypertension, above goal on current therapy. (I10 — Essential "
        "(primary) hypertension)",
        "1. Increase lisinopril to 20 mg daily. 2. Basic metabolic panel in 2 "
        "weeks. 3. Continue home BP log. 4. Low-sodium diet reviewed. 5. Follow "
        "up in 6 weeks.",
        [{"code": "I10", "description": "Essential (primary) hypertension"}],
    ),
    (
        "maria.okafor@clinic.example", 3, 14,
        "Ethan Wright, 24, three days of sore throat, congestion, and cough. No "
        "fever at home. Roommate had similar illness. Exam: temp 99.1, mild "
        "pharyngeal erythema without exudate, no lymphadenopathy, lungs clear. "
        "Rapid strep negative.",
        "24-year-old male with 3 days of sore throat, nasal congestion, and dry "
        "cough. Sick contact (roommate). No subjective fever.",
        "T 99.1F. Mild pharyngeal erythema, no exudate or tonsillar swelling. No "
        "cervical lymphadenopathy. Lungs clear. Rapid strep: negative.",
        "Acute viral upper respiratory infection. (J06.9 — Acute upper "
        "respiratory infection, unspecified)",
        "1. Supportive care: fluids, rest, saline gargles. 2. Ibuprofen PRN "
        "throat pain. 3. Return if fever >101, symptoms >10 days, or shortness "
        "of breath.",
        [{"code": "J06.9", "description": "Acute upper respiratory infection, unspecified"}],
    ),
    (
        "maria.okafor@clinic.example", 4, 30,
        "Priya Sharma, 60, diabetes follow-up. HbA1c last week 7.9, up from 7.2. "
        "Admits more takeout since retiring. On metformin 1000 twice daily. No "
        "hypoglycemia, no vision changes, no foot numbness. Exam normal, "
        "monofilament intact.",
        "60-year-old female, T2DM follow-up. HbA1c risen 7.2 -> 7.9. Reports "
        "dietary drift since retirement. On metformin 1000 mg BID, tolerated. "
        "Denies hypoglycemia, visual changes, or neuropathic symptoms.",
        "BP 128/78, BMI 29. Foot exam: skin intact, monofilament sensation "
        "intact bilaterally, pulses 2+.",
        "Type 2 diabetes mellitus with suboptimal control. (E11.65 — Type 2 "
        "diabetes mellitus with hyperglycemia)",
        "1. Dietitian referral; reviewed carbohydrate targets. 2. Continue "
        "metformin; discussed adding empagliflozin if next A1c >8. 3. Repeat "
        "HbA1c in 3 months. 4. Annual retinal exam scheduled.",
        [{"code": "E11.65", "description": "Type 2 diabetes mellitus with hyperglycemia"}],
    ),
    (
        "sarah.chen@clinic.example", 5, 7,
        "Samuel Osei, 34, twisted left ankle playing soccer yesterday. Able to "
        "bear weight with pain. Swelling over lateral malleolus. No bony "
        "tenderness on palpation of posterior malleolus or fifth metatarsal. "
        "Ottawa rules negative.",
        "34-year-old male with left ankle inversion injury during soccer "
        "yesterday. Weight-bearing possible with pain. No prior ankle injuries.",
        "Left ankle: swelling and ecchymosis over lateral malleolus, tender over "
        "ATFL, no bony tenderness at malleoli or base of 5th metatarsal (Ottawa "
        "negative). Able to bear weight x4 steps.",
        "Left ankle sprain, lateral ligament complex, grade II. (S93.412A — "
        "Sprain of calcaneofibular ligament of left ankle, initial encounter)",
        "1. No imaging indicated (Ottawa negative). 2. RICE protocol, air "
        "stirrup brace. 3. Ibuprofen 400-600 mg PRN. 4. Weight bearing as "
        "tolerated; return in 2 weeks if not improving.",
        [{"code": "S93.412A", "description": "Sprain of calcaneofibular ligament of left ankle, initial encounter"}],
    ),
]


def seed() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(User).limit(1)) is not None:
            print("Seed skipped: users already exist.")
            return

        password_hash = hash_password(DEMO_PASSWORD)  # hash once, reuse — bcrypt is slow by design
        users = {
            email: User(email=email, full_name=name, role=role, password_hash=password_hash)
            for email, name, role in USERS
        }
        patients = [Patient(first_name=f, last_name=l, dob=dob) for f, l, dob in PATIENTS]
        db.add_all([*users.values(), *patients])
        db.flush()  # assign ids before wiring FKs below

        now = datetime.now(timezone.utc)
        for provider_email, patient_idx, days_ago, transcript, s, o, a, p, codes in ENCOUNTERS:
            when = now - timedelta(days=days_ago)
            provider = users[provider_email]
            encounter = Encounter(
                patient_id=patients[patient_idx].id,
                provider_id=provider.id,
                transcript=transcript,
                status=EncounterStatus.saved,
                created_at=when,
                updated_at=when,
            )
            db.add(encounter)
            db.flush()
            db.add(
                NoteVersion(
                    encounter_id=encounter.id,
                    version_number=1,
                    subjective=s,
                    objective=o,
                    assessment=a,
                    plan=p,
                    icd_codes=codes,
                    saved_by=provider.id,
                    saved_at=when,
                )
            )

        db.commit()
        print(f"Seeded {len(USERS)} users, {len(PATIENTS)} patients, {len(ENCOUNTERS)} saved encounters.")
        print(f"All demo logins use password: {DEMO_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
