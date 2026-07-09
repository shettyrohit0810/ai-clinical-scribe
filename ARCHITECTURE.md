# Architecture

Living document — updated at the end of every phase, alongside
PROJECT_STRUCTURE.md and DECISIONS.md. This is the architectural source of
truth for future phases.

## System overview

```mermaid
flowchart LR
    B[Browser SPA<br/>React 18 + TS] -->|HTTPS| N[nginx on EC2<br/>TLS, static SPA,<br/>reverse proxy]
    N -->|/api + /ws → 127.0.0.1:8001| A[FastAPI<br/>gunicorn + uvicorn x2]
    A -->|SQLAlchemy pool<br/>10+5, pre-ping| R[(PostgreSQL<br/>RDS, private)]
    A -->|startup, instance role| S[AWS Secrets Manager]
    A -.->|Phase 2+| L[Anthropic API<br/>haiku: interim drafts<br/>sonnet: final notes + voice-edit patches]
```

- **One deployable unit**: nginx serves the built SPA and proxies `/api`
  (JSON + SSE) and `/ws` (voice edit session, Phase 8) to the backend, which
  binds 127.0.0.1 only. The DB accepts connections solely from the app's
  security group.
- **Ports**: backend is 8001 in every environment; local dev Postgres is host
  port 5433 (see DECISIONS.md for why).

## Request/data flows

**Auth (Phase 1)** — `POST /api/auth/login` verifies bcrypt hash → sets a JWT
(30-min expiry; claims: `sub`, `role`) in an httpOnly cookie. Every protected
route runs `get_current_user`: decode JWT → load user row → check `is_active`
in the DB. The fresh DB check means deactivation takes effect on the user's
very next request despite stateless tokens. The cookie deliberately outlives
the token so the server can distinguish "session expired" (401 + re-auth
modal, Phase 9) from "never logged in".

**Provider isolation (Phase 1)** — every encounter query filters
`provider_id == token user's id` server-side; the id never comes from the
client. Cross-provider reads return 404 (not 403) so encounter ids don't leak
existence. Admins see all rows.

**Note generation (Phase 2, live)** — client flushes its autosave PATCH,
then opens `GET /api/encounters/{id}/generate` (SSE). The route reads the
transcript + template instructions fresh from the DB (no cache, no push
channel — freshness by design), retrieves top-k ICD candidates (local
hashed-BoW embeddings + Python cosine over `icd_codes`), and streams
`claude-sonnet-4-6` (tier=final) or `claude-haiku-4-5` (tier=draft) through
`app/llm.py` — the single LLM gateway (60s timeout, one SDK retry,
max_tokens cap, call counter, structured errors). `app/stream_parser.py`
converts tagged sections into per-section SSE deltas; the four SOAP panes
fill incrementally. Vendor failure → `error` event → calm UI state; the
draft in the DB is untouched. `<no_clinical_content/>` → refusal event; an
empty transcript short-circuits without an LLM call.

**Persistence model** — a *draft* is an `encounters` row with `status=draft`
(DB-backed → survives refresh and works cross-device). Saving appends an
immutable `note_versions` row; versions hang directly off the encounter (no
separate notes table).

**Version history (Phase 4, live)** — the workspace's version panel lists
saved versions via `GET /api/encounters/{id}/versions` (summary rows: number,
saver, timestamp — no note body) and fetches one full version on click via
`GET /api/encounters/{id}/versions/{n}`, read fresh from RDS every time.
Nothing is ever updated or deleted in `note_versions`; the append-only
invariant is enforced by the DB (`UniqueConstraint(encounter_id,
version_number)`) and proven by an exact-value re-read test, not just a row
count.

**ICD-10 search widget (Phase 5, live)** — `GET /api/icd/search?q=<text>`
is a second, direct caller of the same `rank_candidates` function generation
uses internally: provider types a query, gets the top 5 by local
hashed-BoW-embedding cosine, and clicking a result appends
`"{code}: {description}"` into the open note's **Assessment** text. The
289-code catalog (target 250-300) is embedded once at seed time by the same
idempotent upsert script generation's candidates already relied on — no new
search infrastructure, no vendor, no vector DB.

**Admin dashboard (Phase 6, live)** — four surfaces under `/admin`
(`role == admin`, enforced server-side by `require_admin`; the client-side
`RequireAdmin` route guard is a UX nicety, not the boundary):

- **Encounters** — the SAME `GET /api/encounters` providers use, extended
  with `provider_id`/`date_from`/`date_to` query params that are only
  consulted on the admin branch of the existing isolation check. No parallel
  route, no second ownership rule to maintain.
- **Providers** — create (reuses `hash_password`/`User` as-is) and
  activate/deactivate (`PATCH /api/admin/providers/{id}`). Deactivation is
  effective on that provider's very next request — the same
  `get_current_user` DB re-check from Phase 1, unmodified.
- **Templates** — full CRUD (create + partial update; no DELETE, `is_active`
  is the soft delete so encounters keep a valid FK for history). Editing a
  template here needs **zero changes** to `generation.py`: the
  read-at-generation architecture from Phase 2 already reads
  `instructions` fresh from the DB on every generate call, so an admin edit
  simply *is* the next generation's input.
- **Audit log** — `GET /api/admin/audit`, reusing the `audit_log` table and
  `record_audit` helper from Phase 1; every mutation above writes a row in
  the same transaction as the action it records.

**Voice dictation (Phase 7, live)** — entirely client-side; no new backend
surface. `frontend/src/transcription.ts` wraps the browser's Web Speech API
behind a `TranscriptionProvider` interface (`isSupported`, `start(handlers)`,
`stop()`); `frontend/src/useDictation.ts` is the dictation state machine
(idle/listening/paused) built on top of it. Data flow:

1. **Interim/final speech → transcript buffer.** The recognizer emits interim
   text (displayed live, never persisted) and finalized chunks (appended to
   whatever the transcript buffer currently holds — including manual edits
   made between or during dictation bursts — via the existing autosave PATCH
   path, unchanged from earlier phases).
2. **Rolling regeneration.** A self-timed 1s poll fires
   `GET .../generate?tier=draft` (haiku) on a 2s pause since the last final
   chunk, or every 6s of continuous speech, whichever comes first — the exact
   SSE pipeline and stream parser from Phase 2, just invoked with `tier=draft`
   by a timer instead of a button.
3. **Final regeneration.** On Stop Dictation, one `GET .../generate` call at
   the default `tier=final` (sonnet) — same call the manual "Generate note"
   button makes.
4. **Sync guard.** Both auto-triggers above are skipped whenever the
   clinician has hand-edited a SOAP pane since the last generation
   (`noteDirty`, set on any pane's `onChange`) — checked before the
   `EventSource` even opens, so a dirty note is never silently overwritten by
   either the rolling draft or the stop-triggered final. Only the manual
   Generate button bypasses this guard.

Because dictation drives the same `generate` endpoint and stream parser every
other trigger uses, the SOAP panes, ICD candidate list, and history tool-call
flow (Phase 3) all work identically whether a generation was clicked or
auto-triggered by dictation — there is exactly one generation pipeline in
this system, not two.

**Conversational voice editing (Phase 8, live)** — a genuinely different
mode from dictation: not producing a new note, but patching an
already-generated one by spoken command, over a persistent WebSocket
(`WS /ws/encounters/{id}/voice-edit`) instead of one-shot SSE. Data flow:

1. **Speech → one command.** The same browser Web Speech API (via a second
   `TranscriptionProvider`-backed hook, `frontend/src/useVoiceEdit.ts`) runs
   in "one finalized utterance = one command" mode rather than
   "append everything to a buffer" mode — each `onFinal` chunk is sent as
   `{"type": "command", "text": "..."}` over the socket.
2. **Command → one JSON patch, never a regenerated note.** The server reads
   the note fresh from `draft_note` (or the latest saved version if no
   draft exists yet), and makes ONE non-streaming call
   (`llm.complete_json`, sonnet tier) asking the model for exactly one
   patch: `add`, `remove`, `rewrite`, or `move`. `remove`/`move` require the
   model to quote existing section text VERBATIM — the same
   candidate-constrained shape as ICD selection (Phase 2), applied to text
   edits instead of codes.
3. **Patch → mutation, through exactly one function.**
   `app/note_patch.apply_note_patch` is the only code path allowed to turn
   a patch into new section content — pure, unit-tested independent of any
   LLM, and the reason the content-preservation invariant ("every section
   the patch doesn't name survives byte-identical") is structural rather
   than something each call site has to remember to uphold.
4. **Persist + reply.** A successful patch updates `encounter.draft_note`
   (carrying `icd_codes` through untouched — voice edits never touch them)
   and writes an audit row, both in the same transaction; the full
   post-patch note is sent back so the client's state is always a direct
   copy of server-computed truth, never a client-side reapplication of the
   patch. `noteDirty` (Phase 7's guard) is set exactly as it would be for a
   typed pane edit, so a later dictation session's auto-regeneration can't
   silently overwrite a voice edit either.
5. **Graceful failure, same connection.** Malformed client messages, LLM
   failures, non-JSON model output, `{"op": "unclear"}`, and patches that
   fail `apply_note_patch` validation all reply with `{"type": "error", ...}`
   and leave the socket open for the next command — nothing is ever
   partially applied, and one bad command never ends the session.

Dictation and voice-editing are mutually exclusive in the UI (one
microphone, two incompatible ways of using it) — enforced by disabling each
mode's Start button while the other is active, not by anything in either
hook.

**Non-happy-path recovery (Phase 9, live)** — no new backend surface; this
is entirely how the frontend consumes error contracts that already existed
(`"Session expired"` and `"Account deactivated"` since Phase 1/6,
`<no_clinical_content/>` refusal since Phase 2). Two distinct recovery
shapes, both centralized in `frontend/src/api.ts` (the one wrapper every
request already goes through) rather than handled per-component:

1. **Session expiry → recoverable, transparently retried.** `api()`
   catches `401 "Session expired"`, calls `requestReauth()`
   (`frontend/src/sessionExpiry.ts` — a small broker with no React
   dependency, since `api.ts` has no component to render a modal from),
   which `AuthProvider` has registered to show a re-login modal and
   resolve once the physician re-authenticates. `api()` then replays the
   EXACT SAME request once and returns its result to the original caller
   — `flushAutosave`, `saveVersion`, or any future protected call needs no
   awareness that this happened. Concurrent requests expiring together
   share one in-flight re-login promise, so only one modal ever appears.
2. **Deactivation → terminal, whole-app block.** `api()` catches
   `403 "Account deactivated"` and calls `notifyDeactivated()`, which sets
   one flag in `AuthProvider` that replaces the entire routed app with a
   blocking screen. Unlike session expiry this is never retried —
   re-authenticating can't fix a deactivated account, so every subsequent
   call would fail the same way. If a re-auth modal happens to be open
   when deactivation is detected, the deactivation handler explicitly
   closes it and rejects its pending promise (found live: without this,
   the modal became unreachable once the block screen rendered, leaving
   its promise resolved by nothing, forever).

Both paths leave the actual draft untouched by construction: session
expiry never modifies local React state (only the network call was
paused), and deactivation only ever flips `users.is_active` — it never
touches `encounters` or `note_versions`.

## Component responsibilities

| Component | Owns |
|---|---|
| nginx | TLS, static SPA, `/api` proxy with `proxy_buffering off` (SSE), `/ws` upgrade |
| FastAPI app | auth, isolation, prompt construction, stream parsing, audit log |
| PostgreSQL | all state: users, patients, encounters, note versions, templates, ICD codes + embeddings, audit |
| Browser | Web Speech STT (dictation + voice commands), speechSynthesis TTS, SSE/WS consumption |

## Major decisions (details in DECISIONS.md)

- Two-tier models: haiku for interim dictation drafts, sonnet for final notes
  and voice edits (latency budget vs. quality budget).
- Append-only `note_versions`, no separate notes identity table.
- Template freshness by read-at-generation, not a push channel.
- ICD codes: candidate-constrained selection (model chooses from real rows we
  retrieve — codes can't be hallucinated); JSONB embeddings + Python cosine
  at ~300 rows (pgvector would be premature).
- Web Speech API is the STT baseline behind a `TranscriptionProvider`
  interface; server-side streaming STT is a stretch behind the same interface.
- Voice edits are patches, never regenerated notes: one JSON patch per
  spoken command, applied only through `apply_note_patch` (the single
  mutation path), with `remove`/`move` requiring the model to quote
  existing text verbatim rather than describe it.
