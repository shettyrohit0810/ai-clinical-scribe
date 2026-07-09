# Project Structure

Living document — updated at the end of every phase. Dependency rules at the
bottom are enforced by review, not tooling; keep them true.

```
ai-scribe/
├── docker-compose.yml        # local dev Postgres (host port 5433)
├── ARCHITECTURE.md           # system design — source of truth
├── PROJECT_STRUCTURE.md      # this file
├── API_CONTRACTS.md          # endpoint + SSE event contract (frontend mirrors it)
├── DECISIONS.md              # per-phase decision log
├── backend/
│   ├── requirements.txt      # runtime deps (approved stack)
│   ├── requirements-dev.txt  # pytest + httpx (tests only)
│   ├── alembic.ini           # no connection string — env.py injects it
│   ├── alembic/versions/     # migrations (baseline → schema → …)
│   ├── app/
│   │   ├── main.py           # FastAPI app; mounts REST routers under /api, voice_edit under /ws
│   │   ├── config.py         # settings: .env locally, Secrets Manager in prod
│   │   ├── db.py             # pooled engine + get_db() dependency
│   │   ├── models.py         # ORM models — the full schema, index rationale inline
│   │   ├── schemas.py        # Pydantic request/response models
│   │   ├── auth.py           # bcrypt + JWT + get_current_user/require_admin/get_current_user_ws deps
│   │   ├── audit.py          # audit_log writer helper
│   │   ├── llm.py            # THE single Anthropic gateway: tiers, streaming + one-shot JSON calls, timeout, retry, cap
│   │   ├── prompts.py        # all prompts: fixed frame, template framing, ICD candidates, voice-edit patch
│   │   ├── stream_parser.py  # pure incremental tagged-section parser (fuzz-tested)
│   │   ├── note_patch.py     # apply_note_patch — pure, single mutation path for voice-edit patches
│   │   ├── icd.py            # hashed-BoW embed + cosine + rank_candidates
│   │   ├── history.py        # fetch_patient_history impl (patient-scoped, no args)
│   │   ├── seed.py           # idempotent demo data (python -m app.seed)
│   │   ├── seed_icd.py       # 289 real ICD-10 codes, embedded at seed time
│   │   └── routers/
│   │       ├── auth.py       # /api/auth/{login,logout,me}
│   │       ├── admin.py      # /api/admin/* — providers CRUD, template CRUD, audit view
│   │       ├── encounters.py # CRUD+save — provider isolation + admin filters live here
│   │       ├── generation.py # /api/encounters/{id}/generate (SSE)
│   │       ├── icd.py        # /api/icd/search — ICD-10 widget (same rank_candidates as generation)
│   │       ├── templates.py  # /api/templates (public list: active, no instructions)
│   │       ├── voice_edit.py # WS /ws/encounters/{id}/voice-edit — command → patch → apply_note_patch
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
│       ├── test_llm_tool_loop.py   # tool loop vs scripted fake SDK client
│       ├── test_versioning.py      # append-only invariant, list/view, isolation
│       ├── test_icd_search.py      # search endpoint + 289-code catalog integrity
│       ├── test_admin_encounters.py # admin filters + isolation-preserved case
│       ├── test_admin_providers.py  # create/dupe-email/deactivate/audit/403
│       ├── test_admin_templates.py  # CRUD + read-at-generation freshness test
│       ├── test_admin_audit.py      # ordering, entity display, 403 for non-admin
│       ├── test_note_patch.py       # apply_note_patch: 4 ops, malformed patches, content-preservation invariant
│       └── test_voice_edit.py       # WS route: auth/isolation, patch flow, graceful errors, multi-command ordering
├── frontend/
│   ├── vite.config.ts        # dev proxy /api → 8001 (mirrors prod nginx), /ws → 8001 with ws:true
│   └── src/
│       ├── main.tsx          # entry; router lives in App
│       ├── App.tsx           # routes: /login, / , /encounters/*, /admin, /stream-test
│       ├── api.ts            # fetch wrapper: JSON, errors → ApiError(status)
│       ├── auth.tsx          # AuthContext: me/login/logout + RequireAuth + RequireAdmin
│       ├── transcription.ts  # TranscriptionProvider interface + WebSpeechTranscriptionProvider (Web Speech API, client-only, ambient type decls)
│       ├── useDictation.ts   # dictation state machine: start/pause/resume/stop, interim tracking, rolling-regen trigger timer
│       ├── useVoiceEdit.ts   # voice-edit state machine: WebSocket + TranscriptionProvider (command mode) + speechSynthesis TTS w/ interruption
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Dashboard.tsx    # encounter list + New encounter + Admin dashboard link
│       │   ├── NewEncounter.tsx # identity form, template pick, returning match
│       │   ├── Workspace.tsx    # transcript + streaming SOAP panes + autosave + save + version history + ICD search widget + voice dictation (useDictation) + voice editing (useVoiceEdit), mutually exclusive, sharing one noteDirty guard
│       │   ├── AdminDashboard.tsx # tab shell: Encounters / Providers / Templates / Audit log
│       │   └── admin/
│       │       ├── EncountersTab.tsx  # provider + date-range filters over GET /api/encounters
│       │       ├── ProvidersTab.tsx   # list + add form + activate/deactivate
│       │       ├── TemplatesTab.tsx   # list + create/edit modal + activate/deactivate
│       │       └── AuditTab.tsx       # audit log table
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
- LLM access lives in one client module (`llm.py`); routers call it, never
  the Anthropic SDK directly.
- Frontend: `pages/*` talk to the backend only through `api.ts`; `auth.tsx`
  is the only holder of session state.
