# API Contracts

Living document — updated whenever an endpoint or event format changes.
Response models live in `backend/app/schemas.py`; this file is the
human-readable contract the frontend types (`frontend/src/api.ts`) mirror.

## Authentication

Every endpoint below **except** `POST /api/auth/login`, `GET /api/health`,
and `GET /api/dev/stream-test` requires an authenticated session:

- Session = JWT (30-min expiry; claims `sub`, `role`) in an **httpOnly
  cookie** named `access_token`, set by login. The browser attaches it
  automatically (same-origin in dev via the vite proxy, and in prod via
  nginx) — including on EventSource connections. No token ever passes
  through JavaScript.
- Failure modes callers must handle:
  - `401 {"detail": "Not authenticated"}` — no/unknown cookie
  - `401 {"detail": "Session expired"}` — token expired (drives the Phase 9
    re-login-and-retry modal; the cookie deliberately outlives the token so
    the server can distinguish this case)
  - `403 {"detail": "Account deactivated"}` — user was deactivated;
    effective on the very next request
- Provider isolation: encounter routes are scoped to the token's user;
  cross-provider access returns **404** (not 403 — existence must not leak).
  Admins see all encounters.

## Auth

| Method | Path | Body → Response |
|---|---|---|
| POST | `/api/auth/login` | `{email, password}` → `UserOut{id,email,full_name,role}` + sets cookie. 401 identical for unknown email / wrong password. 403 if deactivated. |
| POST | `/api/auth/logout` | — → `{status}` + clears cookie |
| GET | `/api/auth/me` | — → `UserOut` (SPA session probe on load) |

## Encounters

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/encounters` | — → `EncounterSummary[]` (provider-scoped; newest first) |
| POST | `/api/encounters` | `{first_name, last_name, dob, template_id?}` → 201 `EncounterCreated{encounter_id, patient, returning, prior_encounters}`. Patient matched case-insensitively on (first, last, dob); created if absent. |
| GET | `/api/encounters/{id}` | — → `EncounterDetail{...EncounterSummary, transcript, template_id, draft_note, latest_version}` |
| PATCH | `/api/encounters/{id}` | **Autosave endpoint** (client debounces ~3s). `{transcript?, template_id?, draft_note?}` — only fields present in the JSON are applied, so partial patches never wipe sibling state. `draft_note = {subjective, objective, assessment, plan, icd_codes[]}`. → `{status, updated_at}` |
| POST | `/api/encounters/{id}/save` | `{subjective?, objective?, assessment?, plan?, icd_codes?}` → `{version_number, saved_at}`. Append-only: inserts the next `note_versions` row, sets status=saved, clears `draft_note`. |

## Templates

| Method | Path | Response |
|---|---|---|
| GET | `/api/templates` | `TemplateOut[]{id, name, description}` — active templates only. `instructions` are deliberately NOT exposed here (server-side prompt material, read fresh from the DB at generation time). Admin CRUD arrives in Phase 6. |

## Note generation (SSE)

`GET /api/encounters/{id}/generate?tier=final|draft`

- **GET** because the browser consumes it with `EventSource` (GET-only).
  Safe: generation reads its inputs from the DB, and the client flushes its
  autosave PATCH before opening the stream, so the server always generates
  from the freshest transcript.
- `tier=final` (default) → `claude-sonnet-4-6` (quality budget).
  `tier=draft` → `claude-haiku-4-5` (latency budget; Phase 7 rolling
  dictation).
- Template instructions are read from the DB **at generation time** — an
  admin edit is simply present on the next generate (no push channel).
- Empty transcript short-circuits to `no_clinical_content` + `done` without
  an LLM call.
- `Content-Type: text/event-stream`; `Cache-Control: no-cache`;
  `X-Accel-Buffering: no`.

### Event formats (all `data:` fields are JSON)

| event | data | Meaning |
|---|---|---|
| `section` | `{"section": "subjective"\|"objective"\|"assessment"\|"plan", "delta": "text"}` | Incremental text — append to that pane. Sections may interleave with other events; deltas within a section are ordered. |
| `icd_codes` | `[{"code": "M17.11", "description": "..."}]` | Complete list, emitted once (buffered server-side; malformed model JSON degrades to `[]`). Codes are always drawn from the backend-supplied candidate list. |
| `history` | `{"prior_encounters": 3}` | The server-side `fetch_patient_history` tool ran during this generation (returning patients only). Drives the "History referenced: N prior encounters" indicator. Each invocation is also written to `audit_log` (`tool_call:fetch_patient_history`). |
| `reset` | `{}` | The model emitted note text before its tool call (rare — the prompt says call-first). Client must clear all panes + ICD codes; everything after this event is the authoritative note. |
| `no_clinical_content` | `{}` | Input had no clinically meaningful content. Nothing generated or saved. Terminal-ish: followed by `done`. |
| `error` | `{"message": "user-facing text"}` | Vendor/LLM failure after the SDK's one retry. **Terminal — no `done` follows.** Client shows a calm retry state; drafts in the DB are untouched. |
| `done` | `{}` | Successful end of stream. Client closes the EventSource (otherwise it auto-reconnects and re-generates). |

Exactly one terminal event per stream: `done` or `error`.

## Dev

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{status, database}` — unauthenticated; used by deploy runbook |
| GET | `/api/dev/stream-test` | SSE numbers 1–20 @200ms; `event: done` terminator. Infrastructure check (Phase 0 DoD). |
