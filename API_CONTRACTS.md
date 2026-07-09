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

### Client-side recovery (Phase 9)

No new backend surface — both failure modes above were already
distinguished by message/status since Phase 1/6. This documents how the
frontend now consumes that distinction.

- **`401 "Session expired"` → transparent re-auth-and-retry.** Every
  request goes through one shared wrapper (`frontend/src/api.ts`). On this
  specific error, it pauses the call, shows a re-login modal
  (`frontend/src/auth.tsx`), and — once the user re-authenticates — replays
  the EXACT SAME request once, returning that result to the original
  caller as if nothing happened. Callers (autosave, save-as-version, any
  future protected call) need no special-case code; the recovery lives
  entirely in the one place every request already passes through. If
  several requests expire together, they all share one in-flight re-login
  promise — only one modal ever shows (`frontend/src/sessionExpiry.ts`).
  Other 401s (`"Not authenticated"`, `"Invalid session"`) are NOT
  recoverable this way and fall through to the normal `RequireAuth`
  redirect-to-login.
- **`403 "Account deactivated"` → terminal, full-screen block.** Detected
  in the same wrapper; NOT retried (re-authenticating won't fix a
  deactivated account — every subsequent call would 403 again regardless).
  Sets a global flag that replaces the entire routed app with a blocking
  "Account deactivated — your draft is preserved" screen. The draft itself
  is never at risk: deactivation only ever flips `users.is_active`, never
  touches `encounters`/`note_versions`.

See DECISIONS.md Phase 9 for the dedup/interceptor design and an edge case
found and fixed during live testing.

## Auth

| Method | Path | Body → Response |
|---|---|---|
| POST | `/api/auth/login` | `{email, password}` → `UserOut{id,email,full_name,role}` + sets cookie. 401 identical for unknown email / wrong password. 403 if deactivated. |
| POST | `/api/auth/logout` | — → `{status}` + clears cookie |
| GET | `/api/auth/me` | — → `UserOut` (SPA session probe on load) |

## Encounters

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/encounters` | `?provider_id=&date_from=&date_to=` (admin-only filters; **inert for non-admin callers** — see below) → `EncounterSummary[]{..., provider_id, provider_name}` (provider-scoped; newest first) |
| POST | `/api/encounters` | `{first_name, last_name, dob, template_id?}` → 201 `EncounterCreated{encounter_id, patient, returning, prior_encounters}`. Patient matched case-insensitively on (first, last, dob); created if absent. |
| GET | `/api/encounters/{id}` | — → `EncounterDetail{...EncounterSummary, transcript, template_id, draft_note, latest_version}` |
| PATCH | `/api/encounters/{id}` | **Autosave endpoint** (client debounces ~3s). `{transcript?, template_id?, draft_note?}` — only fields present in the JSON are applied, so partial patches never wipe sibling state. `draft_note = {subjective, objective, assessment, plan, icd_codes[]}`. → `{status, updated_at}` |
| POST | `/api/encounters/{id}/save` | `{subjective?, objective?, assessment?, plan?, icd_codes?}` → `{version_number, saved_at}`. Append-only: inserts the next `note_versions` row, sets status=saved, clears `draft_note`. |
| GET | `/api/encounters/{id}/versions` | — → `NoteVersionSummary[]{version_number, saved_by, saved_by_name, saved_at}`, oldest-first. No note body — cheap for the version history panel. |
| GET | `/api/encounters/{id}/versions/{version_number}` | — → `NoteVersionOut{version_number, subjective, objective, assessment, plan, icd_codes, saved_by, saved_at}`. 404 if the version doesn't exist. Read fresh from RDS every call — the append-only table IS the history store, so there's nothing to invalidate. |

**Admin filters on `GET /api/encounters`** — `provider_id`, `date_from`,
`date_to` (all optional, `YYYY-MM-DD` for dates). These are consulted only
on the `role == admin` branch of the existing isolation check
(`if user.role != admin: scope to self` / `elif provider_id: scope to that
provider`). For a non-admin caller the first branch always matches, so the
params are simply never read for them — passing another provider's id
returns your own encounters unchanged, never theirs. This is why the
dashboard's all-encounters table is an extension of this one endpoint
rather than a separate `/admin/encounters` route: one ownership check to
get right, not two.

## Templates

| Method | Path | Response |
|---|---|---|
| GET | `/api/templates` | `TemplateOut[]{id, name, description}` — active templates only. `instructions` are deliberately NOT exposed here (server-side prompt material, read fresh from the DB at generation time). |

## Admin dashboard

Every route below requires `role == admin` (`require_admin` dependency);
a provider gets `403 {"detail": "Admin access required"}`. Every mutation
writes an `audit_log` row via the same `record_audit` helper the rest of
the app uses.

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/admin/providers` | — → `ProviderOut[]{id, email, full_name, role, is_active, created_at}`, role=provider only, ordered by name. |
| POST | `/api/admin/providers` | `{email, full_name, password}` → 201 `ProviderOut`. Email normalized (`.strip().lower()`) to match the exact lookup `login()` performs — a mixed-case email at creation can still log in. `400` on a duplicate email (DB UNIQUE constraint, not just app validation). Audit: `provider_create`. |
| PATCH | `/api/admin/providers/{id}` | `{is_active}` → `ProviderOut`. Scoped to `role == provider` — a target id that isn't a provider (including the admin's own account) 404s; this also rules out admin self-lockout by construction, no separate guard needed. Audit: `provider_activate` \| `provider_deactivate`. Deactivation is effective on the provider's very next request (existing `get_current_user` behavior — see auth.py). |
| GET | `/api/admin/templates` | — → `TemplateAdminOut[]{id, name, description, instructions, is_active, created_by, created_at, updated_at}` — **all** templates including inactive, **with** `instructions` (the admin editing view; contrast with the public `GET /api/templates`). |
| POST | `/api/admin/templates` | `{name, description?, instructions}` → 201 `TemplateAdminOut`. Audit: `template_create`. |
| PATCH | `/api/admin/templates/{id}` | `{name?, description?, instructions?, is_active?}` — only fields present are applied (same `model_fields_set` idiom as `EncounterPatch`). No DELETE endpoint: `is_active=false` is the soft delete. 404 if not found. Audit: `template_update`. |
| GET | `/api/admin/audit` | `?limit=` (1–200, default 100) → `AuditLogEntryOut[]{id, user_id, user_name, action, entity_type, entity_id, created_at}`, newest first. |

## ICD-10 search widget

| Method | Path | Query → Response |
|---|---|---|
| GET | `/api/icd/search` | `?q=<free text>` (2–200 chars) → `IcdCodeItem[]{code, description}`, top 5 by cosine similarity. |

- Ad-hoc search the provider drives by typing (e.g. "knee pain") — distinct
  from the candidate list injected into note generation (same underlying
  `rank_candidates`, called directly instead of from inside a prompt).
- Local embedding + Python cosine only — no vendor call on this path, not
  even a mocked one; fully deterministic.
- `422` if `q` is under 2 characters. `401` unauthenticated (any signed-in
  role, not provider-scoped — the catalog has no patient data to isolate).
- Frontend: results render as a click list in the workspace; clicking a
  result appends `"{code}: {description}"` to the open note's **Assessment**
  text. This is separate from the `icd_codes` chips populated by
  generation — search-widget additions are free text the clinician chose,
  not model output, so they don't need the candidate-constrained provenance
  the generation flow guarantees.

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

### History tool-call flow (returning patients only)

When the encounter's patient has ≥1 prior **saved** encounter, the model is
offered the `fetch_patient_history` tool (never for new patients — they get
a plain single-round stream). The flow within one SSE response:

1. Round 1 streams; the model calls `fetch_patient_history` (prompted to do
   so BEFORE writing note text). The tool takes **no arguments** — the
   backend scopes the fetch to this encounter's patient server-side.
2. On the tool call the server: writes an `audit_log` row
   (`tool_call:fetch_patient_history`), logs an app INFO line, emits the
   `history` SSE event, and returns the newest 3 saved encounters
   (subjective/assessment/plan, truncated) as the tool result.
3. Round 2 streams the actual note as normal `section` events.
4. If the model emitted note text before its tool call (rare), a `reset`
   event precedes `history` — the client discards everything shown so far.

Hard stop after 3 rounds → `error` event. From the client's perspective this
is all one EventSource connection; only the extra `history`/`reset` events
distinguish it from a plain generation.

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

## Voice editing (Phase 8, WebSocket)

`WS /ws/encounters/{encounter_id}/voice-edit`

- **Mounted under `/ws`, not `/api`.** The two live behind separate nginx
  (and dev Vite proxy) location blocks — `/api` is tuned for SSE
  (`proxy_buffering off`, no Upgrade headers); `/ws` carries the
  Upgrade/Connection pair a WebSocket handshake needs. `/ws` was reserved
  in the nginx config since Phase 0 for exactly this route.
- **Auth**: same httpOnly session cookie as every other route, read from
  `websocket.cookies` before `.accept()`. An unauthenticated or
  cross-provider connection is rejected with `websocket.close(code=4401)`
  (no session) or `4404` (encounter not found / not owned — same
  existence-must-not-leak spirit as the REST isolation rule, translated to
  the nearest WebSocket-native mechanism: closing without detail rather
  than an HTTP 404 body).
- **One command in, one patch out.** Every message on the connection is a
  full round trip — no partial/streaming patches, since a patch is a
  handful of short JSON fields, not multi-paragraph prose.

Client → server:
```json
{"type": "command", "text": "add to the assessment that the patient denies fever"}
```

Server → client (exactly one reply per command):
```json
{"type": "patch_applied", "note": {"subjective": "...", "objective": "...", "assessment": "...", "plan": "..."}, "patch": {"op": "add", "section": "assessment", "text": "..."}, "message": "Added to assessment."}
{"type": "error", "message": "user-facing text"}
```

- `note` is the full, authoritative post-patch state of all four SOAP
  sections — the client replaces its local state with this rather than
  reapplying the patch itself, so there is exactly one place (the server)
  that ever computes what a patch does.
- `message` is a short, server-synthesized confirmation ("Added to
  assessment.", "Moved to objective.") spoken back via the browser's
  `speechSynthesis` — never model-authored text, so a TTS confirmation
  can't itself contain hallucinated content.
- `error` covers every failure mode on one connection without ever closing
  it: malformed client JSON, an LLM failure, model output that isn't valid
  JSON, a command the model couldn't map to an edit (`{"op": "unclear"}`),
  and a patch that fails validation (unknown op/section, or `remove`/`move`
  text that isn't an exact match) — each replies with `error` and the loop
  keeps running, ready for the next command.
- **`icd_codes` are untouched by every voice-edit patch.** Voice editing is
  scoped to the four text sections only; the route reads whatever
  `icd_codes` currently exist (draft or latest saved version) and writes
  them straight back unchanged alongside the patched sections, so a voice
  edit can never silently drop the ICD chips.
- Commands are processed strictly in the order received — the server
  awaits one full round trip (LLM call → validate → persist → reply)
  before reading the next message, so a burst of consecutive commands is
  serialized rather than raced against a shared "current note" read.

See DECISIONS.md Phase 8 for the patch schema, the `apply_note_patch`
validation rules, and why remove/move require the model to quote existing
text verbatim.

## Voice dictation (Phase 7, client-side only)

No new backend endpoints. Dictation reuses `GET /api/encounters/{id}/generate`
verbatim (`tier=draft` for rolling regen, `tier=final`/no param for the
stop-triggered generation) — this section documents the client-side flow that
drives those existing calls.

- **Transcription never touches the backend.** The browser's Web Speech API
  (`window.SpeechRecognition` / `webkitSpeechRecognition`) streams interim and
  final results entirely client-side, behind a `TranscriptionProvider`
  interface (`frontend/src/transcription.ts`). Only the resulting text ever
  reaches the server, via the same autosave PATCH the manual transcript
  textarea already used.
- **Transcript is a single append-only buffer.** Finalized speech chunks are
  appended to whatever the transcript field currently holds — including any
  manual edits typed between or during dictation bursts. Interim (not-yet-
  final) text is displayed live but never committed to the buffer or sent to
  the server.
- **Rolling regeneration trigger**: `useDictation` (`frontend/src/useDictation.ts`)
  self-times two conditions via a 1s poll — 2s of silence since the last
  finalized chunk ("a pause"), or 6s elapsed since the last regeneration
  (force-refresh during continuous speech) — and calls
  `GET .../generate?tier=draft` (haiku) when either fires and there's
  unregenerated speech pending.
- **Final regeneration**: on Stop Dictation, one `GET .../generate` call at
  `tier=final` (sonnet, the existing default).
- **Dirty-note guard**: if the clinician has hand-edited a SOAP pane since the
  last generation (`noteDirty`), BOTH the rolling draft trigger and the
  stop-triggered final trigger are skipped — no network call, panes untouched.
  This is a uniform rule across both auto-trigger types; only an explicit
  manual "Generate note" click bypasses it. See DECISIONS.md Phase 7 for why.
