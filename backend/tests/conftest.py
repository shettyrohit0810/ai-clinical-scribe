"""Test fixtures.

Strategy: point DATABASE_URL at a dedicated `scribe_test` database BEFORE any
app module is imported — the app's own engine then targets the test DB and no
dependency-override plumbing is needed. Schema comes from Base.metadata
(create_all once per run); tables are truncated between tests so every test
starts clean. LLM calls (Phase 2+) are mocked everywhere — tests stay
deterministic, fast, and free.
"""

import os

# Must happen before importing anything from app.* (engine binds at import).
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://scribe:scribe@localhost:5433/scribe_test"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.auth import hash_password
from app.db import SessionLocal, engine
from app.main import app
from app.models import Base, Encounter, EncounterStatus, Patient, User, UserRole

TEST_PASSWORD = "correct-horse-battery"


def _ensure_test_database() -> None:
    """Create scribe_test if missing (CREATE DATABASE needs autocommit)."""
    admin = create_engine(
        "postgresql+psycopg://scribe:scribe@localhost:5433/postgres",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = 'scribe_test'")
        )
        if not exists:
            conn.execute(text("CREATE DATABASE scribe_test"))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema():
    _ensure_test_database()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate everything between tests — cheap at this table count."""
    yield
    with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    return TestClient(app)


# ---- data helpers ------------------------------------------------------------


@pytest.fixture
def users(db):
    """Two providers + one admin, all with TEST_PASSWORD."""
    pw = hash_password(TEST_PASSWORD)
    made = {
        "provider_a": User(
            email="a@test.example", full_name="Provider A",
            role=UserRole.provider, password_hash=pw,
        ),
        "provider_b": User(
            email="b@test.example", full_name="Provider B",
            role=UserRole.provider, password_hash=pw,
        ),
        "admin": User(
            email="admin@test.example", full_name="Admin",
            role=UserRole.admin, password_hash=pw,
        ),
    }
    db.add_all(made.values())
    db.commit()
    return made


@pytest.fixture
def encounter_for_a(db, users):
    patient = Patient(first_name="Pat", last_name="Ient", dob="1980-01-01")
    db.add(patient)
    db.flush()
    encounter = Encounter(
        patient_id=patient.id,
        provider_id=users["provider_a"].id,
        transcript="knee pain for two weeks",
        status=EncounterStatus.saved,
    )
    db.add(encounter)
    db.commit()
    return encounter


def login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
