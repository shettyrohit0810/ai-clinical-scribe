# AI Clinical Scribe

A provider-facing AI clinical documentation platform. A physician dictates
or pastes an encounter transcript; Claude generates a structured SOAP note
(Subjective, Objective, Assessment, Plan) with candidate-constrained ICD-10
codes; the physician edits by hand or by voice, saves an append-only
version, and an admin can manage providers, templates, and the audit trail.

Built across 11 phases as a take-home technical screen. This README is the
entry point; [ARCHITECTURE.md](ARCHITECTURE.md), [DECISIONS.md](DECISIONS.md),
[API_CONTRACTS.md](API_CONTRACTS.md), and [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)
are the living documents of record — every claim below is backed by one of
them, and by tests or live verification described there.

## Features

**Core scribe workflow**
- Paste or dictate a transcript; generate a structured SOAP note over a
  live-streaming connection (SSE) — panes fill progressively, not after a
  spinner.
- Three note templates (New Patient Evaluation, Orthopedic Follow-up,
  Urgent Care) visibly reshape structure and tone; template edits by an
  admin take effect on the very next generation, no refresh needed.
- Candidate-constrained ICD-10 selection: the model can only choose codes
  from a locally-retrieved candidate list (cosine similarity over a
  289-code catalog) — codes cannot be hallucinated. A separate search
  widget lets a provider look up and insert codes ad hoc.
- Returning patients: the model can call a zero-argument
  `fetch_patient_history` tool mid-generation to pull the last three saved
  visits for continuity — server-scoped to the current patient, never a
  model-supplied argument.
- Append-only version history with an inline word-level diff between any
  two saved versions.

**Voice**
- **Dictation** — free-form speech-to-text via the browser's Web Speech
  API streams into the transcript; a rolling Haiku-tier draft regenerates
  on a natural pause or every ~6s of continuous speech, and a Sonnet-tier
  final generation runs once on Stop. Manual edits to the transcript are
  preserved and never overwritten by an in-progress dictation session.
- **Conversational voice editing** — once a note exists, spoken commands
  ("add to the assessment that...", "remove...", "move the swelling note
  to objective") are turned into a single structured JSON patch
  (`add`/`remove`/`rewrite`/`move`) by the model and applied through one
  pure, unit-tested function. `remove`/`move` require the model to quote
  existing text verbatim, so a patch can never silently rewrite content it
  wasn't asked to touch. The assistant speaks a short confirmation back
  (`speechSynthesis`); starting to talk again interrupts it immediately.
- Dictation and voice editing are mutually exclusive in the UI (one
  microphone, two different jobs) and both respect a manual-edit guard: a
  hand-edited or voice-edited note is never silently overwritten by an
  automatic regeneration.

**Accounts & admin**
- JWT + httpOnly cookie auth; provider data isolation enforced server-side
  (cross-provider access 404s, never 403 — existence itself doesn't leak).
- Admin dashboard: all-encounters view (provider/date filters), provider
  management (create/deactivate/reactivate), template CRUD, and a
  full audit log.
- Session recovery, not just error messages: a mid-action session expiry
  shows a re-login modal and transparently retries the exact request that
  failed — no re-typing, no lost draft. A deactivated account fails safe
  with a clear, honest message; the draft is never at risk (deactivation
  only ever flips one flag, never touches note data).

## Browser support

**Google Chrome is recommended.** Voice dictation and conversational voice
editing depend on the browser's `SpeechRecognition` (Web Speech API) and
`speechSynthesis`, which are non-standard and most completely implemented
in Chrome/Chromium and Edge. In an unsupported browser, both voice features
detect this and degrade gracefully — a "Voice dictation/editing isn't
supported in this browser" note appears and typed/pasted transcripts and
manual note editing work exactly as usual; nothing else in the app depends
on Web Speech.

## Documentation map

| Doc | What's in it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System diagram, request/data flows per feature, component responsibilities |
| [DECISIONS.md](DECISIONS.md) | Full per-phase decision log — every non-obvious choice and why, including two premise-check callouts and every post-ship bug found and fixed during live testing |
| [API_CONTRACTS.md](API_CONTRACTS.md) | Every endpoint, the SSE event contract, and the WebSocket voice-edit protocol |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | Annotated file tree + dependency boundaries |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | Full happy-path walkthrough script |
| [DEMO_FAILURES.md](DEMO_FAILURES.md) | &lt;90s scripts for the three non-happy-path demos |
| [WALKTHROUGH_NOTES.md](WALKTHROUGH_NOTES.md) | Talking points and anticipated questions for the technical walkthrough |
| [FINAL_CHECKLIST.md](FINAL_CHECKLIST.md) | Pre-recording consistency audit + demo readiness checklist |

## Project layout

```
backend/   FastAPI + SQLAlchemy 2.x + Alembic (Python 3.12)
frontend/  React 18 + TypeScript + Vite + Tailwind (SPA, served by nginx in prod)
infra/     DEPLOY.md runbook, nginx config, systemd unit
```

Full annotated tree: [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md).

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

# 3. Frontend (separate terminal; dev server proxies /api and /ws to :8001)
cd frontend
npm install
npm run dev
```

Seed demo data and run tests:

```bash
cd backend
.venv/bin/python -m app.seed        # idempotent demo data
.venv/bin/python -m app.seed_icd    # 289-code ICD-10 catalog (idempotent) —
                                    # required: generation candidates and the
                                    # search widget both read this table
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/   # 137 tests: unit + route + WS + multi-router integration
```

```bash
cd frontend
npx tsc --noEmit                    # typecheck (no separate frontend test suite)
```

Open http://localhost:5173 and sign in. `/stream-test` hosts the SSE
progressive-streaming infrastructure check from Phase 0.

## Seeded logins

All demo accounts use password `KyronDemo` (demo stage props, not
secrets). Seed data is deliberately shaped for the demo: Margaret Thompson
has three prior saved encounters across two providers, making her the
returning-patient / history-injection demo case.

| Email | Role |
|---|---|
| sarah.chen@clinic.example | provider |
| james.patel@clinic.example | provider |
| maria.okafor@clinic.example | provider |
| admin@clinic.example | admin |

## Known limitations

- **Server-side streaming STT was not implemented.** The Web Speech API
  baseline is deliberately built behind a `TranscriptionProvider`
  interface specifically so a server-side STT vendor (mic → WebSocket →
  backend → vendor) could be swapped in later as a one-line change at the
  construction site — this was an explicitly optional stretch goal (Phase
  10) and was consciously not attempted so it wouldn't delay required
  deliverables. It remains unbuilt.
- **This development environment had no microphone or speaker hardware.**
  Voice features (dictation and voice editing) were verified end-to-end
  against the real backend and the real Anthropic API using a scripted
  mock of the browser's `SpeechRecognition`/`speechSynthesis` event
  contract — never a mocked LLM. The developer then separately performed
  real-microphone-and-speaker verification in Chrome on their own machine
  and reported results (including one real bug found and fixed — see
  DECISIONS.md Phase 8). A real-hardware smoke test before any further use
  is still good practice, as with any voice feature.
- **No local embedding/vector infra beyond ~300 rows' worth.** ICD-10
  candidate retrieval uses deterministic hashed bag-of-words vectors +
  Python cosine, an explicit, settled choice at this catalog size (see
  DECISIONS.md Phase 2) — not a vector database. Revisit only if the
  catalog grows an order of magnitude.

## Deployment

See [infra/DEPLOY.md](infra/DEPLOY.md) — numbered runbook for RDS (private),
EC2 + IAM + Secrets Manager, nginx + HTTPS, and systemd.
