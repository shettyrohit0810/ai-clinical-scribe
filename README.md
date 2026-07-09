# AI Clinical Scribe

Provider-facing AI clinical documentation platform. A physician pastes or
dictates an encounter transcript; the AI generates a structured SOAP note
(Subjective, Objective, Assessment, Plan) with suggested ICD-10 codes; the
physician edits by hand or by voice, and saves.

> Full architecture docs, seeded logins, and design-decision write-up land in
> the final phase. This README grows with the build.

## Layout

```
backend/   FastAPI + SQLAlchemy 2.x + Alembic (Python 3.12)
frontend/  React 18 + TypeScript + Vite + Tailwind (SPA, served by nginx in prod)
infra/     DEPLOY.md runbook, nginx config, systemd unit
```

## Local development

Requirements: Python 3.12, Node 20+, Docker.

```bash
# 1. Database (local Postgres in Docker)
docker compose up -d

# 2. Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # local-only config; git-ignored
alembic upgrade head
# Port 8001 is this project's port everywhere (dev + prod nginx upstream).
uvicorn app.main:app --reload --port 8001

# 3. Frontend (separate terminal; dev server proxies /api to :8001)
cd frontend
npm install
npm run dev
```

Seed demo data and run tests:

```bash
cd backend
.venv/bin/python -m app.seed        # idempotent demo data
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/   # auth matrix + provider isolation
```

Open http://localhost:5173 and sign in. `/stream-test` hosts the SSE
progressive-streaming infrastructure check.

## Seeded logins

All demo accounts use password `ScribeDemo1!` (demo stage props, not secrets):

| Email | Role |
|---|---|
| sarah.chen@clinic.example | provider |
| james.patel@clinic.example | provider |
| maria.okafor@clinic.example | provider |
| admin@clinic.example | admin |

## Deployment

See [infra/DEPLOY.md](infra/DEPLOY.md) — numbered runbook for RDS (private),
EC2 + IAM + Secrets Manager, nginx + HTTPS, and systemd.
