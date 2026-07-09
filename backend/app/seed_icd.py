"""Seed the icd_codes table:  python -m app.seed_icd

~290 real ICD-10-CM codes across common outpatient specialties (Phase 5
target: 250-300). The mechanism (upsert by code, embed at seed time) is
unchanged from the Phase 2 starter set — Phase 5 only adds rows. Idempotent:
re-running updates descriptions and re-embeds, never duplicates.
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

    # ---- Phase 5 expansion (below) --------------------------------------

    # Musculoskeletal / orthopedic (additional sites & diagnoses)
    ("M25.521", "Pain in right elbow"),
    ("M25.522", "Pain in left elbow"),
    ("M25.531", "Pain in right wrist"),
    ("M25.532", "Pain in left wrist"),
    ("M25.551", "Pain in right hip"),
    ("M25.552", "Pain in left hip"),
    ("M25.571", "Pain in right ankle and joints of right foot"),
    ("M25.572", "Pain in left ankle and joints of left foot"),
    ("M75.101", "Unspecified rotator cuff tear or rupture of right shoulder, not specified as traumatic"),
    ("M75.102", "Unspecified rotator cuff tear or rupture of left shoulder, not specified as traumatic"),
    ("M75.30", "Calcific tendinitis of unspecified shoulder"),
    ("M76.61", "Achilles tendinitis, right leg"),
    ("M76.62", "Achilles tendinitis, left leg"),
    ("M70.60", "Trochanteric bursitis, unspecified hip"),
    ("M71.30", "Other bursal cyst, unspecified site"),
    ("M79.601", "Pain in right arm"),
    ("M79.602", "Pain in left arm"),
    ("M79.604", "Pain in right leg"),
    ("M79.605", "Pain in left leg"),
    ("M79.7", "Fibromyalgia"),
    ("M19.90", "Unspecified osteoarthritis, unspecified site"),
    ("M19.011", "Primary osteoarthritis, right shoulder"),
    ("M19.012", "Primary osteoarthritis, left shoulder"),
    ("M15.9", "Polyosteoarthritis, unspecified"),
    ("M06.9", "Rheumatoid arthritis, unspecified"),
    ("M10.9", "Gout, unspecified"),
    ("M81.0", "Age-related osteoporosis without current pathological fracture"),
    ("M54.16", "Radiculopathy, lumbar region"),
    ("M54.12", "Radiculopathy, cervical region"),
    ("M53.1", "Cervicobrachial syndrome"),
    ("M99.03", "Segmental and somatic dysfunction of lumbar region"),
    ("S39.012A", "Strain of muscle, fascia and tendon of lower back, initial encounter"),
    ("S16.1XXA", "Strain of muscle and tendon at neck level, initial encounter"),
    ("S43.401A", "Sprain of unspecified site of right shoulder joint, initial encounter"),
    ("S43.402A", "Sprain of unspecified site of left shoulder joint, initial encounter"),
    ("S63.401A", "Unspecified sprain of right wrist, initial encounter"),
    ("S63.402A", "Unspecified sprain of left wrist, initial encounter"),
    ("S83.91XA", "Unspecified injury of right knee, initial encounter"),
    ("S83.92XA", "Unspecified injury of left knee, initial encounter"),
    ("S52.501A", "Unspecified fracture of the lower end of right radius, initial encounter for closed fracture"),
    ("S52.502A", "Unspecified fracture of the lower end of left radius, initial encounter for closed fracture"),
    ("S92.301A", "Unspecified fracture of right foot, initial encounter for closed fracture"),
    ("S82.101A", "Unspecified fracture of upper end of right tibia, initial encounter for closed fracture"),
    ("M65.9", "Synovitis and tenosynovitis, unspecified"),
    ("M77.10", "Lateral epicondylitis, unspecified elbow"),
    ("M77.00", "Medial epicondylitis, unspecified elbow"),
    ("G56.00", "Carpal tunnel syndrome, unspecified upper limb"),
    ("G57.00", "Lesion of sciatic nerve, unspecified lower limb"),

    # Respiratory (additional)
    ("J44.9", "Chronic obstructive pulmonary disease, unspecified"),
    ("J44.1", "Chronic obstructive pulmonary disease with acute exacerbation"),
    ("J45.20", "Mild intermittent asthma, uncomplicated"),
    ("J45.40", "Moderate persistent asthma, uncomplicated"),
    ("G47.33", "Obstructive sleep apnea (adult) (pediatric)"),
    ("J90", "Pleural effusion, not elsewhere classified"),
    ("J93.9", "Pneumothorax, unspecified"),
    ("R09.81", "Nasal congestion"),
    ("R05.9", "Cough, unspecified"),
    ("J31.0", "Chronic rhinitis"),
    ("J32.9", "Chronic sinusitis, unspecified"),
    ("J35.01", "Chronic tonsillitis"),
    ("R06.00", "Dyspnea, unspecified"),
    ("J98.01", "Acute bronchospasm"),
    ("Z87.891", "Personal history of nicotine dependence"),
    ("F17.210", "Nicotine dependence, cigarettes, uncomplicated"),

    # Cardiovascular (additional)
    ("I11.9", "Hypertensive heart disease without heart failure"),
    ("I63.9", "Cerebral infarction, unspecified"),
    ("I73.9", "Peripheral vascular disease, unspecified"),
    ("I80.209", "Phlebitis and thrombophlebitis of unspecified deep vessels of unspecified lower extremity"),
    ("I83.90", "Asymptomatic varicose veins of unspecified lower extremity"),
    ("I95.9", "Hypotension, unspecified"),
    ("R00.0", "Tachycardia, unspecified"),
    ("R00.1", "Bradycardia, unspecified"),
    ("R00.2", "Palpitations"),
    ("I49.9", "Cardiac arrhythmia, unspecified"),
    ("I34.0", "Nonrheumatic mitral (valve) insufficiency"),
    ("I35.0", "Nonrheumatic aortic (valve) stenosis"),
    ("E78.0", "Pure hypercholesterolemia"),
    ("E78.2", "Mixed hyperlipidemia"),
    ("R94.31", "Abnormal electrocardiogram [ECG] [EKG]"),

    # Endocrine / metabolic (additional)
    ("E10.9", "Type 1 diabetes mellitus without complications"),
    ("E11.40", "Type 2 diabetes mellitus with diabetic neuropathy, unspecified"),
    ("E11.22", "Type 2 diabetes mellitus with diabetic chronic kidney disease"),
    ("E11.319", "Type 2 diabetes mellitus with unspecified diabetic retinopathy without macular edema"),
    ("E05.90", "Thyrotoxicosis, unspecified without thyrotoxic crisis or storm"),
    ("E04.1", "Nontoxic single thyroid nodule"),
    ("E04.2", "Nontoxic multinodular goiter"),
    ("E27.40", "Unspecified adrenocortical insufficiency"),
    ("E83.42", "Hypomagnesemia"),
    ("E87.6", "Hypokalemia"),
    ("E87.5", "Hyperkalemia"),
    ("E16.2", "Hypoglycemia, unspecified"),
    ("E88.9", "Metabolic disorder, unspecified"),
    ("R73.03", "Prediabetes"),
    ("Z79.4", "Long term (current) use of insulin"),
    ("Z79.84", "Long term (current) use of oral hypoglycemic drugs"),

    # Gastrointestinal (additional)
    ("K21.00", "Gastro-esophageal reflux disease with esophagitis, without bleeding"),
    ("K29.70", "Gastritis, unspecified, without bleeding"),
    ("K30", "Functional dyspepsia"),
    ("K58.9", "Irritable bowel syndrome without diarrhea"),
    ("K57.30", "Diverticulosis of large intestine without perforation or abscess without bleeding"),
    ("K57.92", "Diverticulitis of intestine, part unspecified, without perforation or abscess without bleeding"),
    ("K76.0", "Fatty (change of) liver, not elsewhere classified"),
    ("K74.60", "Unspecified cirrhosis of liver"),
    ("K80.20", "Calculus of gallbladder without cholecystitis without obstruction"),
    ("K64.9", "Unspecified hemorrhoids"),
    ("K40.90", "Unilateral inguinal hernia, without obstruction or gangrene"),
    ("K42.9", "Umbilical hernia without obstruction or gangrene"),
    ("K92.2", "Gastrointestinal hemorrhage, unspecified"),
    ("R19.7", "Diarrhea, unspecified"),
    ("K59.1", "Functional diarrhea"),
    ("B19.20", "Unspecified viral hepatitis C without hepatic coma"),
    ("K71.6", "Toxic liver disease with hepatitis, not elsewhere classified"),

    # Genitourinary (additional)
    ("N40.0", "Benign prostatic hyperplasia without lower urinary tract symptoms"),
    ("N40.1", "Benign prostatic hyperplasia with lower urinary tract symptoms"),
    ("N52.9", "Male erectile dysfunction, unspecified"),
    ("N94.6", "Dysmenorrhea, unspecified"),
    ("N92.0", "Excessive and frequent menstruation with regular cycle"),
    ("N95.1", "Menopausal and female climacteric states"),
    ("N76.0", "Acute vaginitis"),
    ("N73.9", "Female pelvic inflammatory disease, unspecified"),
    ("N41.0", "Acute prostatitis"),
    ("N23", "Unspecified renal colic"),
    ("N18.3", "Chronic kidney disease, stage 3 (moderate)"),
    ("N39.3", "Stress incontinence (female) (male)"),
    ("N39.46", "Mixed incontinence"),
    ("R31.9", "Hematuria, unspecified"),
    ("R35.0", "Frequency of micturition"),
    ("Z30.9", "Encounter for contraceptive management, unspecified"),

    # Neurology (additional)
    ("G43.109", "Migraine with aura, not intractable, without status migrainosus"),
    ("G44.209", "Tension-type headache, unspecified, not intractable"),
    ("G45.9", "Transient cerebral ischemic attack, unspecified"),
    ("G40.909", "Epilepsy, unspecified, not intractable, without status epilepticus"),
    ("G62.9", "Polyneuropathy, unspecified"),
    ("G56.10", "Other lesions of median nerve, unspecified upper limb"),
    ("G51.0", "Bell's palsy"),
    ("G93.3", "Postviral fatigue syndrome"),
    ("R25.1", "Tremor, unspecified"),
    ("R26.9", "Unspecified abnormalities of gait and mobility"),
    ("R55", "Syncope and collapse"),
    ("R41.3", "Other amnesia"),
    ("R47.01", "Aphasia"),
    ("G30.9", "Alzheimer's disease, unspecified"),

    # Mental health (additional)
    ("F33.1", "Major depressive disorder, recurrent, moderate"),
    ("F31.9", "Bipolar disorder, unspecified"),
    ("F90.9", "Attention-deficit hyperactivity disorder, unspecified type"),
    ("F43.10", "Post-traumatic stress disorder, unspecified"),
    ("F40.10", "Social phobia, unspecified"),
    ("F42.9", "Obsessive-compulsive disorder, unspecified"),
    ("F51.01", "Primary insomnia"),
    ("F17.200", "Nicotine dependence, unspecified, uncomplicated"),
    ("F10.20", "Alcohol dependence, uncomplicated"),
    ("Z71.9", "Counseling, unspecified"),
    ("R45.851", "Suicidal ideations"),

    # Dermatology (additional)
    ("L20.9", "Atopic dermatitis, unspecified"),
    ("L30.9", "Dermatitis, unspecified"),
    ("L40.9", "Psoriasis, unspecified"),
    ("L71.9", "Rosacea, unspecified"),
    ("L57.0", "Actinic keratosis"),
    ("L82.1", "Other seborrheic keratosis"),
    ("B07.9", "Viral wart, unspecified"),
    ("B35.9", "Dermatophytosis, unspecified"),
    ("L60.0", "Ingrowing nail"),
    ("L02.91", "Cutaneous abscess, unspecified"),
    ("L98.9", "Disorder of the skin and subcutaneous tissue, unspecified"),
    ("L29.9", "Pruritus, unspecified"),
    ("L50.9", "Urticaria, unspecified"),
    ("L81.4", "Other melanin hyperpigmentation"),

    # Eyes / ENT (additional)
    ("H52.4", "Presbyopia"),
    ("H25.9", "Unspecified age-related cataract"),
    ("H40.9", "Unspecified glaucoma"),
    ("H93.19", "Tinnitus, unspecified ear"),
    ("H81.10", "Benign paroxysmal vertigo, unspecified ear"),
    ("H61.23", "Impacted cerumen, bilateral"),
    ("R04.0", "Epistaxis"),
    ("H72.90", "Unspecified perforation of tympanic membrane, unspecified ear"),
    ("H90.3", "Sensorineural hearing loss, bilateral"),
    ("H91.90", "Unspecified hearing loss, unspecified ear"),
    ("H11.9", "Disorder of conjunctiva, unspecified"),
    ("H01.009", "Unspecified blepharitis unspecified eye, unspecified eyelid"),

    # Infectious disease (additional)
    ("B02.9", "Zoster without complications"),
    ("B27.90", "Infectious mononucleosis, unspecified without complication"),
    ("A69.20", "Lyme disease, unspecified"),
    ("B37.3", "Candidiasis of vulva and vagina"),
    ("J02.0", "Streptococcal pharyngitis"),
    ("A08.4", "Viral intestinal infection, unspecified"),
    ("B08.20", "Exanthema subitum [sixth disease], unspecified"),
    ("B34.2", "Coronavirus infection, unspecified"),

    # Allergy / immunology (additional)
    ("T78.40XA", "Allergy, unspecified, initial encounter"),
    ("Z88.0", "Allergy status to penicillin"),
    ("Z91.010", "Allergy to peanuts"),
    ("J30.1", "Allergic rhinitis due to pollen"),
    ("L23.7", "Allergic contact dermatitis due to plants, except food"),

    # Injury / trauma (additional)
    ("S00.83XA", "Contusion of other part of head, initial encounter"),
    ("S00.93XA", "Contusion of unspecified part of head, initial encounter"),
    ("S01.81XA", "Laceration without foreign body of other part of head, initial encounter"),
    ("S06.0X0A", "Concussion without loss of consciousness, initial encounter"),
    ("T14.90XA", "Injury, unspecified, initial encounter"),
    ("T22.091A", "Burn of unspecified degree of other part of right shoulder and upper limb, except wrist and hand, initial encounter"),
    ("T23.001A", "Burn of unspecified degree of right hand, unspecified site, initial encounter"),
    ("S60.919A", "Unspecified injury of unspecified hand, initial encounter"),
    ("S90.919A", "Unspecified injury of unspecified foot, initial encounter"),

    # Pediatric (additional)
    ("J06.0", "Acute laryngopharyngitis"),
    ("J38.5", "Laryngeal spasm"),
    ("R10.4", "Other and unspecified abdominal pain"),
    ("R11.10", "Vomiting, unspecified"),
    ("P59.9", "Neonatal jaundice, unspecified"),
    ("R62.50", "Unspecified lack of expected normal physiological development in childhood"),
    ("F80.9", "Developmental disorder of speech and language, unspecified"),
    ("F84.0", "Autistic disorder"),
    ("Z00.121", "Encounter for routine child health examination with abnormal findings"),
    ("Z00.129", "Encounter for routine child health examination without abnormal findings"),

    # Women's health / obstetric (additional)
    ("Z34.90", "Encounter for supervision of normal pregnancy, unspecified trimester"),
    ("Z32.01", "Encounter for pregnancy test, result positive"),
    ("Z01.419", "Encounter for gynecological examination without abnormal findings"),
    ("N63.0", "Unspecified lump in unspecified breast"),
    ("N60.9", "Unspecified benign mammary dysplasia, unspecified breast"),
    ("O21.0", "Mild hyperemesis gravidarum"),

    # Nutrition / general (additional)
    ("E44.0", "Moderate protein-calorie malnutrition"),
    ("R63.4", "Abnormal weight loss"),
    ("R63.5", "Abnormal weight gain"),
    ("Z68.30", "Body mass index [BMI] 30.0-30.9, adult"),
    ("R73.9", "Hyperglycemia, unspecified"),
    ("R79.89", "Other specified abnormal findings of blood chemistry"),
    ("Z71.3", "Dietary counseling and surveillance"),
    ("Z13.220", "Encounter for screening for lipoid disorders"),
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
