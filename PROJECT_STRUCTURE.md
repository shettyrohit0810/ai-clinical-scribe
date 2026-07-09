# Project Structure

Living document — updated at the end of every phase. Dependency rules at the
bottom are enforced by review, not tooling; keep them true.

```
ai-scribe/
├── docker-compose.yml        # local dev Postgres (host port 5433)
├── ARCHITECTURE.md           # system design — source of truth
├── PROJECT_STRUCTURE.md      # this file
├── DECISIONS.md              # per-phase decision log
├── backend/
│   ├── requirements.txt      # runtime deps (approved stack)
│   ├── requirements-dev.txt  # pytest + httpx (tests only)
│   ├── alembic.ini           # no connection string — env.py injects it
│   ├── alembic/versions/     # migrations (baseline → schema → …)
│   ├── app/
│   │   ├── main.py           # FastAPI app; mounts all routers under /api
│   │   ├── config.py         # settings: .env locally, Secrets Manager in prod
│   │   ├── db.py             # pooled engine + get_db() dependency
│   │   ├── models.py         # ORM models — the full schema, index rationale inline
│   │   ├── schemas.py        # Pydantic request/response models
│   │   ├── auth.py           # bcrypt + JWT + get_current_user/require_admin deps
│   │   ├── audit.py          # audit_log writer helper
│   │   ├── llm.py            # THE single Anthropic gateway: tiers, timeout, retry, cap
│   │   ├── prompts.py        # all prompts: fixed frame, template framing, ICD candidates
│   │   ├── stream_parser.py  # pure incremental tagged-section parser (fuzz-tested)
│   │   ├── icd.py            # hashed-BoW embed + cosine + rank_candidates
│   │   ├── history.py        # fetch_patient_history impl (patient-scoped, no args)
│   │   ├── seed.py           # idempotent demo data (python -m app.seed)
│   │   ├── seed_icd.py       # 64 real ICD-10 codes, embedded at seed time
│   │   └── routers/
│   │       ├── auth.py       # /api/auth/{login,logout,me}
│   │       ├── encounters.py # CRUD+save — provider isolation lives here
│   │       ├── generation.py # /api/encounters/{id}/generate (SSE)
│   │       ├── templates.py  # /api/templates (list; admin CRUD in Phase 6)
│   │       └── dev.py        # /api/dev/stream-test (SSE smoke route)
│   └── tests/
│       ├── conftest.py       # scribe_test DB, per-test truncation, client fixture
│       ├── test_auth.py      # login matrix, expiry, deactivation
│       ├── test_isolation.py # provider A ⇏ provider B's encounters
│       ├── test_stream_parser.py   # char-by-char chunk fuzzing, refusal, bad JSON
│       ├── test_encounters_flow.py # returning match, autosave, append-only save
│       ├── test_generation.py      # mocked-LLM SSE: tiers, candidates, errors
│       ├── test_icd.py             # embedding determinism + relevance
│       ├── test_history.py         # history block + route tool events + audit
│       └── test_llm_tool_loop.py   # tool loop vs scripted fake SDK client
├── frontend/
│   ├── vite.config.ts        # dev proxy /api → 8001 (mirrors prod nginx)
│   └── src/
│       ├── main.tsx          # entry; router lives in App
│       ├── App.tsx           # routes: /login, / (protected), /stream-test
│       ├── api.ts            # fetch wrapper: JSON, errors → ApiError(status)
│       ├── auth.tsx          # AuthContext: me/login/logout + RequireAuth
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Dashboard.tsx    # provider's encounter list + New encounter
│       │   ├── NewEncounter.tsx # identity form, template pick, returning match
│       │   └── Workspace.tsx    # transcript + streaming SOAP panes + autosave + save
│       └── StreamTest.tsx    # Phase 0 SSE infrastructure check
└── infra/
    ├── DEPLOY.md             # numbered AWS runbook
    ├── nginx/ai-scribe.conf
    └── systemd/ai-scribe.service
```

## Dependency boundaries

- `models.py` imports nothing from the app (only SQLAlchemy). Everything may
  import models.
- `routers/*` do HTTP concerns + queries; they import `auth`, `schemas`,
  `models`, `db`. Nothing imports from `routers`.
- `auth.py` owns every security primitive; routers never touch jwt/bcrypt
  directly.
- LLM access (Phase 2+) will live in a single client module; routers call it,
  never the Anthropic SDK directly.
- Frontend: `pages/*` talk to the backend only through `api.ts`; `auth.tsx`
  is the only holder of session state.
