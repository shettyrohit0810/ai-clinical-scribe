# Architecture Decisions Log

Running log, updated at the end of every phase. The repo is the source of
truth; if code conflicts with an entry here, the newest approved decision wins
and the conflict gets named explicitly.

---

## Phase 0 — Walking skeleton on real infra

**Goal:** prove the riskiest plumbing (SSE streaming through nginx + HTTPS on
EC2) before writing any product code.

- **Repo layout** — `backend/`, `frontend/`, `infra/`. Monorepo, one deployable
  unit: nginx serves the SPA build and reverse-proxies `/api` to gunicorn on
  localhost. Simple to deploy, simple to explain.
- **API prefix** — every backend route lives under `/api` so the nginx routing
  rule is a single location block and the SPA fallback never collides with it.
- **SSE smoke route** — `/api/dev/stream-test` streams numbers 1–20 at 200 ms.
  It exists solely to validate `proxy_buffering off` end-to-end before the real
  note-generation stream is built on the same transport. Kept in the repo
  (dev-only router) as evidence of infra-first sequencing.
- **Config** — `pydantic-settings` reads a git-ignored `.env` locally. In
  production, `AWS_SECRET_NAME` is set and `app/config.py` fetches one JSON
  secret from AWS Secrets Manager via the EC2 instance role at startup, cached
  in the settings object. No secrets in the repo or in systemd unit files.
- **DB engine** — single SQLAlchemy engine module (`app/db.py`) with
  `pool_size=10, max_overflow=5, pool_pre_ping=True`; rationale for each
  setting is a comment block in that file. No request ever opens its own
  connection — everything goes through the pooled sessionmaker dependency.
- **Alembic baseline** — an intentionally empty revision proves the migration
  pipeline (local Docker Postgres and RDS both reach `head`) before any schema
  exists. Phase 1+ schema changes are ordinary revisions on top.
- **Frontend scaffold** — hand-rolled minimal Vite + React 18 + TS + Tailwind v4
  (vite plugin, no tailwind.config needed). No router yet; Phase 0 is a single
  shell page (login placeholder + SSE test panel). Adding `react-router-dom`
  is proposed for Phase 1 (needs approval — outside the literal approved list).

- **Ports (machine-specific collisions found during build)** — local dev DB
  binds host **5433** (an orphaned `kyron-scribe-db` container from a deleted
  earlier attempt holds 5432 on this laptop); the backend runs on **8001**
  everywhere (another project's Django server owns 8000 locally, and using one
  port in dev and prod keeps nginx/systemd/vite-proxy configs identical in
  shape).

### Interfaces established

- `GET /api/health` → `{status, database}` — used by the deploy runbook.
- `GET /api/dev/stream-test` → `text/event-stream`, 20 events then `done`.

### Deferred (structured TODOs in code)

- Real schema (Phase 1), auth (Phase 1), LLM client module (Phase 2).

---

## Phase 1 — Auth, roles, schema, seeds

- **Full schema in one migration** — all 7 tables landed together even though
  data arrives per phase: the model was designed up front; later phases add
  rows, not tables. Index rationale lives inline in `models.py` (verified
  `(provider_id, created_at DESC)` in pg_indexes).
- **Auth libraries** — PyJWT + bcrypt (the direct realization of the specced
  "JWT + bcrypt"; no passlib — unmaintained). All security primitives in
  `app/auth.py`; routers never touch jwt/bcrypt.
- **Cookie outlives token** (8h vs 30min) — deliberately, so the server can
  answer "Session expired" distinctly from "Not authenticated"; that message
  drives the Phase 9 re-auth-and-retry modal.
- **is_active checked in DB per request** — deactivation is immediate despite
  stateless JWTs. Login failures are indistinguishable for unknown email vs
  wrong password (no account enumeration).
- **Isolation returns 404, not 403** — a 403 would confirm a foreign
  encounter id exists; existence itself must not leak.
- **Prod boot guard** — app refuses to start in production on the dev JWT
  secret; missing config fails loudly at startup.
- **Audit rows share the action's transaction** — an action can never commit
  without its audit entry.
- **react-router-dom v7 approved and added.** Frontend date-only values (DOB)
  are formatted without `new Date(iso)` (UTC-midnight off-by-one).
- **Tests** — dedicated `scribe_test` DB created on demand; DATABASE_URL is
  overridden before app import so the app's own engine points at it (no
  dependency-override plumbing). 9 tests green, 0 warnings: auth matrix,
  expiry, deactivation (both paths), isolation by id and list, admin sees all.

### Interfaces established

- `POST /api/auth/login|logout`, `GET /api/auth/me` (UserOut)
- `GET /api/encounters` (EncounterSummary[]), `GET /api/encounters/{id}`
  (EncounterOut) — provider-scoped, admin sees all

---

## Phase 2 — Core scribe workflow

### Approved design rationales (reviewed and settled with the user)

- **Tagged XML streaming (over JSON output)** — the model emits
  `<subjective>…</subjective>` etc. rather than a JSON object because the
  stream must be parsed *incrementally*: a partial opening/closing tag is
  trivially detectable and cheap to hold back (at most `len(tag)-1` chars),
  whereas a partially received JSON string is unparseable until the document
  closes — which would force buffer-then-dump and kill progressive panes.
  Tags also fail soft: a malformed section damages one pane, not the note.
  The parser is pure and fuzz-tested char-by-char (test_stream_parser.py).
- **Retry strategy = SDK-native `max_retries=1`** — the spec's "one retry
  with backoff on transient failures" is delegated to the Anthropic SDK,
  which retries connection errors, 408/429 and 5xx with exponential backoff.
  Hand-rolling retry logic the vendor SDK already implements would add code
  to defend and subtle bugs to own ("leverage the platform"). Anything that
  survives the retry becomes one structured `("error", message)` event —
  callers never see an exception, the UI shows a calm retry state, drafts
  are untouched.
- **Local ICD semantic search (APPROVED — keep; no embedding vendor)** —
  candidate retrieval uses deterministic feature-hashed bag-of-words vectors
  (256 dims, md5 bucketing, L2-normalized) stored as JSONB float arrays with
  Python cosine, exactly the settled storage contract. Rationale: Anthropic
  has no embeddings endpoint; a second vendor means a second API key, spend
  ceiling, and failure mode; and ICD-10 descriptions are short literal
  clinical phrases where token overlap IS the semantic signal at ~300 rows
  ("knee pain" → M25.56x family, covered by tests). Decision per user:
  do not introduce another embedding provider/vendor unless it becomes
  necessary later; the swap remains a one-function change (`embed_text`)
  plus a re-seed.

### Other Phase 2 decisions

- **`encounters.draft_note` JSONB added** — Phase 2's autosave spec requires
  persisting "transcript + unsaved edits", but the settled schema had no home
  for unsaved note text. draft_note is mutable workspace scratch; versions
  remain the immutable record; save clears it. One column, no new table.
- **SSE generate is GET** — EventSource only speaks GET; safe because
  generation reads inputs from the DB and the client flushes its autosave
  PATCH before opening the stream (freshest transcript guaranteed).
- **Server-side stream parsing** — the tagged-section parser lives in the
  backend (pure module, char-by-char fuzz-tested), so the client just appends
  deltas per section. icd_codes are buffered (partial JSON is useless) and
  malformed JSON degrades to [] rather than failing the note.
- **Empty transcript short-circuits** — refusal outcome is certain, so no
  LLM call is spent discovering it.
- **Retry = SDK max_retries=1** — the spec's "one retry with backoff"
  delegated to the SDK rather than hand-rolled (it retries connection
  errors, 408/429, 5xx with exponential backoff).
- **Templates list omits `instructions`** — prompt material stays
  server-side; providers choose by name/description. Admin CRUD (Phase 6)
  is where instructions become visible/editable.
- **3 templates seeded now** (spec names them in Phase 6) so the Phase 2
  selector demos real behavior; verified live that Urgent Care visibly
  reshapes output (2 sentences/section + RETURN PRECAUTIONS line).

### Interfaces established

- `POST /api/encounters` → EncounterCreated{returning, prior_encounters}
- `PATCH /api/encounters/{id}` — autosave {transcript?, template_id?, draft_note?}
- `POST /api/encounters/{id}/save` → {version_number, saved_at}
- `GET /api/encounters/{id}/generate?tier=final|draft` — SSE: section /
  icd_codes / no_clinical_content / error / done
- `GET /api/templates` → TemplateOut[]
- `llm.stream_completion(model, system, user_prompt)` → ("delta"|"end"|"error", payload)

---

## Phase 3 — Patient history injection (tool use)

- **Real tool use, not the fallback** — the model calls
  `fetch_patient_history` mid-generation; the backend executes it and the
  loop continues (spec's preferred design; the authorized backend-side
  fallback was not needed — integration took well under the 3h budget).
- **Server-side patient scoping: the tool takes NO arguments** — the backend
  resolves "which patient" from the encounter being generated, never from
  model output. Rationale: any parameter (name, id) would make patient
  selection a model decision that must then be validated against
  authorization rules — a prompt-injection surface and a validation burden.
  With a zero-argument tool there is nothing to validate and no path to
  another patient's history: containment by construction, not by validation.
  It also keeps the audit trail honest — the logged invocation is exactly
  the query that ran.
- **Tool offered only to returning patients** — new patients get a plain
  single-round stream. Cheaper (no tool tokens), simpler, and the
  returning-vs-new behavioral difference is architectural rather than
  prompt-dependent.
- **Optimistic streaming + reset event** — the alternative was to buffer
  round-1 output until stop_reason proves no tool call is coming, but that
  destroys progressive rendering for every new-patient generation (the
  common case) to guard against a rare reorder. Instead deltas forward
  live, and the one bad case — text emitted before the tool call — is
  handled by a `reset` SSE event (client clears panes, server restarts its
  parser; everything after reset is authoritative). The prompt's
  "call the tool FIRST" instruction makes reset a safety net, not a code
  path users see. Covered by unit tests at both the llm-loop and route
  level.
- **History limited to the newest 3 SAVED encounters, sections truncated
  (400 chars)** — three reasons: (1) token budget — history rides inside
  the generation request, and unbounded history would crowd out the
  transcript that must dominate the note; (2) clinical recency — the last
  few visits carry the interval-change signal a follow-up note needs, while
  a 15-visit dump buries it; (3) latency/cost — history tokens are billed
  and re-read on the post-tool round. Drafts are excluded because they have
  no signed note to reference. The caps are module constants
  (history.MAX_ENCOUNTERS / MAX_SECTION_CHARS), trivially tunable.
- **Every invocation is evidenced twice** — an `audit_log` row
  (`tool_call:fetch_patient_history`) and an app INFO log line showing
  round accounting (`stop=tool_use` → executed → `stop=end_turn`).
- **Verified live**: sonnet called the tool (chip at ~3s); the note cited
  "up from 3/10 at her last visit" — a figure that exists only in the prior
  saved note. Haiku (draft tier) also calls the tool, so Phase 7 dictation
  drafts will be history-aware for free. New patient stream: no history
  event, no tool round.

### Interfaces established

- SSE events `history {prior_encounters}` and `reset {}` (API_CONTRACTS.md)
- `llm.stream_note_generation(model, system, user_prompt, history_provider)`
- `history.build_history_block(db, encounter)` / `count_prior_saved(db, encounter)`

---

## Phase 4 — Versioning + history UI

- **Summary/detail split for versions** (`NoteVersionSummary` vs
  `NoteVersionOut`) — the panel lists potentially many versions per
  encounter; a list response carrying four Text columns per row per version
  is waste the UI never renders until a row is clicked. `GET .../versions`
  returns id/name/timestamp only; `GET .../versions/{n}` fetches one full
  note on demand. No new abstraction — same shape as `EncounterSummary` vs
  `EncounterDetail` already in the codebase.
- **`saved_by_name` added via join, not a second round trip** — the panel
  needs a human name, not a raw user id, so `list_versions` joins `User` in
  the one query rather than resolving names client-side (would mean either
  N follow-up requests or shipping the whole user table).
- **No caching layer for version reads** — every `GET .../versions/{n}` hits
  Postgres directly. The table is append-only and tiny per encounter (single
  digits in practice), so a cache would add invalidation complexity to solve
  a load problem that doesn't exist yet.
- **Ordering: API returns oldest-first (matches the btree), UI shows
  newest-first (matches how a clinician re-checks a save)** — the reversal
  happens client-side in the panel component; the backend index order stays
  the natural `(encounter_id, version_number)` scan with no ORDER BY
  DESC index to justify.
- **The invariant test is exact-value, not just row-count** — beyond
  asserting two rows exist, it re-reads v1 after v2 is written and asserts
  every field still equals what was originally posted. Row-count alone
  can't distinguish append-only from update-in-place; the field-level
  re-read can.

### Interfaces established

- `GET /api/encounters/{id}/versions` → `NoteVersionSummary[]`
- `GET /api/encounters/{id}/versions/{version_number}` → `NoteVersionOut`

---

## Phase 5 — ICD-10 semantic search widget

- **Catalog expanded to 289 real codes (target 250-300), same seed
  mechanism, no new infrastructure** — `seed_icd.py` gained ~225 rows across
  specialties absent or thin in the Phase 2 starter set (rotator cuff,
  epicondylitis, radiculopathy, COPD/OSA, arrhythmia subtypes, diabetes
  complications, GERD/IBS/diverticulitis, BPH/incontinence, migraine
  subtypes/TIA, bipolar/PTSD/OCD, psoriasis/rosacea, cataract/glaucoma,
  zoster/Lyme, common fracture/sprain/burn codes, pediatric and OB
  encounters, malnutrition/BMI). The upsert-by-code seed function was
  already idempotent and vendor-agnostic — expansion is pure data, zero
  logic changes. **Local embedding + Python cosine kept exactly as
  approved; no external embedding provider or vector database introduced,
  per explicit instruction.**
- **A real search endpoint, distinct from generation's internal
  candidates** — Phase 2 only used `rank_candidates` inside the generation
  prompt (candidate-constrained selection). Phase 5's spec calls for a
  provider-driven widget ("search = embed query → cosine → top 5 → click
  appends"), which is a different access pattern: synchronous, ad-hoc,
  triggered by typing, no LLM in the loop at all. `GET /api/icd/search`
  reuses `rank_candidates` directly — the exact same local embed+cosine
  function, called from a new caller rather than duplicated.
- **Click appends to Assessment text, not to the `icd_codes` chip array**
  — the spec says "click appends code+description to the open note's
  Assessment," which is the clinician's own annotation (free text), not a
  model-selected, candidate-constrained code. Keeping the two paths
  separate preserves the generation flow's provenance guarantee (every
  chip in `icd_codes` came from the model choosing among backend-supplied
  candidates) without overloading it with manually-added codes that never
  went through that constraint.
- **No provider scoping on `/icd/search`** — the ICD catalog is reference
  data with no patient information; every other endpoint's isolation model
  (provider-scoped, 404 across providers) doesn't apply because there is
  nothing to isolate. Any authenticated user (provider or admin) can search.
- **Tests stay LLM-free, including the spec's exact scenario** — the "knee
  pain" → M25.56x-family test lives at both layers: `test_icd.py` already
  covered `rank_candidates` directly (Phase 2); `test_icd_search.py` now
  covers the same scenario through the HTTP endpoint, plus a
  catalog-integrity check (250-300 codes, no duplicates) so a future data
  edit that breaks the range or introduces a dup fails CI, not a demo.

### Interfaces established

- `GET /api/icd/search?q=<text>` → `IcdCodeItem[]` (top 5, cosine-ranked)

---

## Phase 6 — Admin dashboard

- **All-encounters filtering extends the existing endpoint — no
  `/admin/encounters` route** — per explicit instruction to avoid
  duplicating provider/encounter logic. `provider_id`/`date_from`/`date_to`
  are query params on the SAME `GET /api/encounters` providers already use.
  The isolation rule stays exactly where it was (one `if` in
  `list_encounters`); the admin filters slot into the `elif` branch of that
  same check rather than a parallel implementation that could drift out of
  sync or get the ownership rule wrong a second way. Verified explicitly by
  test: a provider passing another provider's `provider_id` never sees that
  provider's data — the param is simply inert for them, not "ANDed" (an
  earlier draft of this test assumed AND semantics and had to be corrected
  to match the actual, safer `if/elif` behavior).
- **Provider create/deactivate reuses existing primitives, not new ones** —
  `POST /api/admin/providers` calls `hash_password` and constructs a `User`
  row exactly as any other code path would; no parallel password or account
  logic exists. Email is normalized (`.strip().lower()`) at creation to
  exactly match what `login()` queries against, so a provider created with
  a mixed-case email can still log in on the first try.
- **Deactivate is scoped to `role == provider`, which eliminates admin
  self-lockout by construction** — an earlier draft added an explicit
  "cannot deactivate your own account" check; it was dead code, because the
  route's own `role != provider` guard already 404s an admin's own account
  before that check could ever run (an admin's row has role=admin, never
  role=provider, and there is no promotion/demotion endpoint that could
  change that). Removed the redundant check rather than keep code that
  implied a code path that could never execute — the scoping rule alone is
  the correct explanation for why self-lockout can't happen here.
- **No template DELETE endpoint — `is_active=false` is the soft delete** —
  `encounters.template_id` is a nullable FK with no cascade; hard-deleting
  a template referenced by a saved historical encounter would either
  violate that FK or silently orphan the reference. `is_active` already
  existed on the model for exactly this reason. Deactivating removes a
  template from the provider-facing picker and from generation eligibility
  (`generation.py` already skips inactive templates) without touching any
  historical data.
- **Template freshness required ZERO changes to generation.py** — the
  read-at-generation architecture (Phase 2) already reads
  `template.instructions` fresh from the DB on every generate call. Admin
  editing a row in place via `PATCH /api/admin/templates/{id}` is exactly
  the write side of that existing design. The Phase 6 test
  (`test_updated_template_takes_effect_on_next_generation_no_refresh`)
  proves this end-to-end: create a template, generate once, edit the
  template as admin, generate again on the same encounter in the same
  session — the second prompt reflects the new instructions and none of
  the old ones, with no cache to bust anywhere in the path.
- **Audit log view reuses the existing `audit_log` table and
  `record_audit` helper** — no new logging mechanism. `GET /api/admin/audit`
  joins `User` for display names and is backed by the pre-existing
  `ix_audit_log_created_at` index (declared in Phase 1, unused by any route
  until now). Every admin mutation in this phase (provider create,
  activate, deactivate, template create, update) calls `record_audit` in
  the same request/transaction as the mutation itself, matching the
  existing atomicity guarantee (`audit.py`: an action can never commit
  without its audit row).
- **Client-side `RequireAdmin` is UX only, not the security boundary** —
  it exists so a provider never sees admin-only UI, but every admin API
  route enforces `role == admin` server-side via `require_admin`
  regardless of what the client does or doesn't render.

### Interfaces established

- `GET /api/encounters?provider_id=&date_from=&date_to=` (admin-only
  filters, extension of the existing route)
- `GET/POST /api/admin/providers`, `PATCH /api/admin/providers/{id}`
- `GET/POST /api/admin/templates`, `PATCH /api/admin/templates/{id}`
- `GET /api/admin/audit?limit=`

---

## Phase 7 — Voice dictation with live SOAP updates

- **Zero backend changes.** The entire feature is client-side: STT via the
  browser's Web Speech API, and regeneration reuses the existing
  `GET /api/encounters/{id}/generate?tier=draft|final` SSE endpoint exactly as
  built in Phase 2 (the `tier=draft` param already existed for this purpose —
  Phase 2's contract literally names it "Phase 7 rolling dictation"). No
  parallel generation pipeline, no new router. All 74 backend tests pass
  unmodified before and after this phase.
- **`TranscriptionProvider` interface, not a direct `webkitSpeechRecognition`
  call site** — `frontend/src/transcription.ts` defines
  `{isSupported, start(handlers), stop()}` and `useDictation`/`Workspace.tsx`
  only ever talk to that interface. Browser Web Speech is the guaranteed-
  working implementation for this screen; a Phase 10 stretch (server-side
  streaming STT: mic → WebSocket → backend → vendor) would implement the same
  interface, making the swap a one-line change at the construction site
  rather than a rewrite of the dictation UI or its sync logic.
- **Self-timed pause/max-interval polling instead of the recognizer's own
  speech-boundary events** — `onspeechend`/`onaudioend` firing semantics
  differ enough across Chrome/Safari's `webkitSpeechRecognition`
  implementations in continuous mode to be an unreliable trigger. Instead
  `useDictation` runs a 1s `setInterval` checking two wall-clock conditions it
  controls itself: 2s since the last finalized chunk (a pause), or 6s since
  the last regeneration (force-refresh during long continuous speech) —
  directly implementing the spec's "on each pause OR every ~6s of new
  finalized text."
- **Two-tier model choice is the latency answer, unchanged from Phase 2** —
  rolling regeneration uses `claude-haiku-4-5` so the SOAP panes stay
  responsive during active dictation; the stop-triggered generation uses
  `claude-sonnet-4-6` once, for the quality-weighted final note. This is the
  exact Phase 2 tier split, invoked with a new caller (auto-triggered, not
  button-clicked) rather than a new mechanism.
- **Transcript is a single editable buffer; dictation only appends to it** —
  `onFinal` reads the CURRENT transcript value (`getTranscript()`) fresh at
  commit time, not a value captured when dictation started, and appends the
  new chunk to it. This is what makes manual edits made between or during
  dictation bursts (including corrections typed while paused) survive: the
  next spoken chunk lands after whatever text — hand-typed or dictated —
  currently occupies the buffer. Interim (unfinalized) text is displayed but
  never written to the buffer or sent to the server.
- **Dirty-note guard applies uniformly to BOTH auto-trigger types, not just
  rolling regen** — the spec's "manual edits ... preserved between bursts"
  requirement is satisfied by tracking `noteDirty` (set on any SOAP-pane
  `onChange`) and having `generate({auto: true})` return immediately —
  before opening an `EventSource` — whenever `noteDirty` is true. This check
  fires identically whether the auto-call came from the rolling-draft timer
  or from Stop's final-tier call: a clinician who edited the Assessment
  mid-dictation should not have Stop silently overwrite that edit just
  because it's the "final" tier. Only the manual "Generate note" button
  ignores the guard (explicit user action always wins); it also clears
  `noteDirty` on completion so auto-updates resume.
- **Deferred pane-clearing** — `generate()`'s existing per-call clearing
  logic was changed to clear a SOAP pane only when the first real content for
  it arrives (first `section`/`icd_codes` event), not eagerly at call time.
  Rolling auto-regeneration fires every few seconds during a live session;
  clearing panes up front would blank visible content for the round-trip
  duration on every rolling regen, a visible flicker the manual
  once-per-click flow never had to avoid.
- **`optsRef` latest-ref pattern to avoid stale closures in the timer** —
  `useDictation`'s 1s check-interval and the Web Speech recognizer's
  event handlers are long-lived relative to `Workspace` re-renders; every
  transcript change gives `generate()` (and therefore
  `onRollingRegenerate`/`onFinalRegenerate`) a new identity. Closing over
  hook options directly at `start()`-time would freeze those callbacks to
  whatever `transcript`/`noteDirty` existed at that moment, letting a long
  session fire regeneration against stale state. Fixed by routing every read
  through a ref updated on every render (`optsRef.current = opts`) and never
  read during render — the same pattern used elsewhere in this codebase for
  autosave flushing.
- **`hadErrorRef` guards against an infinite restart loop** — the Web Speech
  recognizer's `onend` auto-restarts a still-"listening" session (Chrome ends
  "continuous" sessions on its own after silence, without the user pausing or
  stopping). Found live: a permission-denial error fires `onerror` then
  `onend`, and a naive "still listening? restart" rule retried the same
  failing permission forever. `onError` now also drops state to `"idle"` and
  clears the poll timer immediately, and `onEnd`'s auto-restart additionally
  requires `!hadErrorRef.current` (reset per fresh session) — a dead
  recognizer never shows Listening/Pause/Stop controls, and never retries a
  condition that can't succeed without user action (e.g. granting mic
  access).
- **Post-ship fix: transcript textarea was read-only while listening** — the
  initial implementation set `readOnly={dictation.state === "listening"}` and
  spliced the live interim guess directly into the displayed value, to avoid
  a keystroke racing against interim text being rewritten every recognition
  tick. That satisfied the sync-safety goal but violated the actual spec:
  "providers should be able to manually edit the transcript while dictating."
  Root cause was conflating two different things in one value — the
  committed, editable buffer and the algorithmically-driven live guess. Fix:
  the textarea's `value` is now always just the committed `transcript` (never
  read-only), and the interim guess is displayed separately underneath as a
  "Hearing: …" readout. `onFinal` already read `getTranscript()` fresh at
  commit time (see "single editable buffer" above), so no change was needed
  to `useDictation.ts` for typed edits made mid-listening to be respected —
  only the display/editability coupling in `Workspace.tsx` was wrong.
  Verified live: typed a manual correction while the mock recognizer was
  actively "listening," then emitted a further final chunk — the correction
  and the new dictated text both landed in the transcript in the order
  typed/spoken, and the Stop-triggered sonnet generation correctly
  incorporated both.
- **Post-ship fix: `no-speech` surfaced as an application error** — Chrome
  routinely fires a `SpeechRecognition` `error` event with `event.error ===
  "no-speech"` whenever a recognition round ends without capturing audio,
  most visibly right after the app itself calls `stop()`/`pause()`. This is
  the API's own expected terminal signal, not a failure — the spec's own
  description of the code is "generally not a fatal condition." The
  original `onerror` handler treated every error identically, so a routine
  stop would flash a "Dictation error: no-speech" banner at the clinician
  for no actionable reason. Fixed at the source in `transcription.ts`:
  `no-speech` now returns early from `onerror` without calling
  `handlers.onError`, so `useDictation`'s state machine never learns about
  it as an error (`hadErrorRef` stays false). `onend` still fires right
  after, exactly as it does for any other session end, and already handles
  both outcomes correctly: silent auto-restart if the app is still in
  `"listening"` state (mid-session no-speech), or a clean no-op if the app
  itself just stopped/paused (state is already `"paused"`/`"idle"` by the
  time `onend` runs). All other error codes (`not-allowed`,
  `audio-capture`, `network`, etc.) are unaffected and continue to surface
  through the existing banner. Verified live with a scripted mock: no-speech
  fired mid-listening auto-restarts with no banner; no-speech fired right
  after Stop settles cleanly into idle with no banner; `not-allowed` still
  shows "Microphone access was denied."
- **Testing limitation, stated plainly**: this sandboxed environment has no
  real microphone. End-to-end verification used a scripted mock of both
  `window.SpeechRecognition` and `window.webkitSpeechRecognition` (matching
  the real API's event contract: `onresult`/`onerror`/`onend`) driving the
  REAL backend over the REAL SSE pipeline — not a mocked LLM. Verified live:
  rolling haiku regeneration on the pause trigger; a manual pane edit sets
  `noteDirty` and shows the warning banner; subsequent rolling AND
  stop-triggered regeneration both correctly skip while dirty (no network
  call); manual "Generate note" still works regardless of dirty state; a
  clean (non-dirty) session's Stop correctly triggers the sonnet final
  generation with real, correct content. A real-microphone smoke test is
  recommended before recording the walkthrough.

### Interfaces established

No new backend interfaces — see API_CONTRACTS.md "Voice dictation" section
for the client-side contract (`TranscriptionProvider`, dictation state
machine, rolling/final trigger conditions).

---

## Phase 8 — Conversational voice editing

**Scope check before starting this phase**: the kickoff instruction referred
to "the existing WebSocket endpoint" and "keep `apply_note_patch` as the
single source of truth." Neither existed anywhere in the codebase before
this phase — confirmed via a graph search and a full-repo grep before
writing any code. What DID already exist, since Phase 0, was the
*reservation* for this: `infra/nginx/ai-scribe.conf` has carried a separate
`/ws/` location block since the very first commit, and `ARCHITECTURE.md`'s
system diagram has said "`/ws` (voice edit session, Phase 8)" since Phase 0,
and `llm.py`'s own docstring has said "sonnet for final notes **and
voice-edit commands**" since Phase 2. So Phase 8 is where that advance
planning gets built for the first time — not a continuation of code that
was silently assumed into existence. Noted here so the record is accurate;
the instruction's intent (reuse the reserved `/ws` path, reuse the existing
Anthropic client/prompt module, make patch application the one mutation
path) is exactly what got built.

- **Patch, never regenerate** — the spec's central constraint. Every voice
  command becomes exactly one small JSON patch (`add`/`remove`/`rewrite`/
  `move`) via a single non-streaming LLM call, applied by
  `app/note_patch.apply_note_patch` — the ONLY function in the codebase
  allowed to mutate note section content from a voice command.
  `routers/voice_edit.py` never writes model output into the note directly;
  it always goes through this function, mirroring how `apply_note_patch`'s
  name itself is meant to read: singular, load-bearing, load-bearing being
  the whole point.
- **`remove`/`move` require the model to quote existing text VERBATIM,
  never describe it** — the same candidate-constrained philosophy as ICD
  selection (Phase 2: "codes can't be hallucinated because the model
  chooses only from real rows we retrieved"), applied to text edits: the
  model can't silently rewrite or shorten content it wasn't asked to touch,
  because `apply_note_patch` validates the quoted `text` is an exact
  substring of the current section before removing/moving anything. An
  inexact quote (paraphrased, summarized, wrong case) fails validation and
  produces a graceful error instead of a wrong edit. `rewrite` is
  deliberately the ONLY operation allowed to replace a section wholesale —
  `add`/`remove`/`move` only ever touch the specific text named in the
  patch, which is also what makes the content-preservation invariant
  trivial to guarantee: every section a patch doesn't name is copied
  verbatim, by construction, not by care taken in each branch.
- **One-shot, non-streaming completion (`llm.complete_json`), not
  `stream_completion`** — a patch is a handful of short JSON fields, not a
  multi-paragraph note; there is nothing to progressively render, so
  streaming would add protocol complexity (chunk framing, partial-JSON
  handling) to solve a problem that doesn't exist here. Same client
  singleton, same never-raises resilience contract (`("ok"|"error", ...)`)
  as `stream_completion` — one gateway module, two shapes of call for two
  shapes of output, not two gateways.
- **Sonnet tier for voice edits, per a decision already on record since
  Phase 2** — `llm.py`'s docstring has said "sonnet for final notes and
  voice-edit commands (quality budget)" since it was written. A voice edit
  is a low-frequency, high-precision action (the clinician is trusting a
  single round trip to correctly and exactly modify their note) — the
  opposite risk profile from Phase 7's rolling dictation drafts, where
  haiku's speed matters more than any single draft being perfect since it
  gets regenerated every few seconds anyway. Nothing to relitigate;
  building Phase 8 just cashes in a tier choice made six phases ago.
- **`{"op": "unclear"}` as the model's own escape hatch** — mirrors
  `<no_clinical_content/>` from note generation (Phase 2): rather than
  forcing the model to force-fit every ambiguous utterance ("um, hang on")
  into one of four operations, the prompt gives it an explicit way to say
  "this wasn't an edit." Reuses the exact same validation path as any other
  malformed patch (`"unclear"` is simply not in `VALID_OPS`), except the
  router intercepts it first to send a friendlier message than the generic
  "Unrecognized operation" text a truly malformed patch gets.
- **WebSocket auth is a separate function
  (`auth.get_current_user_ws`), not a generalization of
  `get_current_user`** — the two transports fail differently by necessity:
  an HTTP dependency signals failure by raising `HTTPException`, which
  FastAPI converts to a response; a WebSocket route has no response object
  to attach a status to and must explicitly `websocket.close(code=...)`
  instead. Rather than bend the existing, working HTTP auth dependency to
  serve two different failure-handling contracts, `get_current_user_ws`
  duplicates the ~10 lines of token/user-resolution logic and returns
  `None` on any failure, leaving the close code to the caller. Small,
  understandable duplication in the same size class as `require_admin`
  already is, versus a genuinely riskier change to a security-critical
  function every other route depends on.
- **draft_note is written per-command, not just left to the existing 3s
  autosave** — the WebSocket route persists `encounter.draft_note` to the
  DB on every successful patch (plus an audit row, same
  `record_audit`-in-the-same-transaction pattern as every other mutation
  in this codebase). This is the direct backend-side equivalent of the
  autosave guarantee manually-typed edits already have: a voice-edited
  draft must survive a refresh or device switch the same way a typed edit
  does. The frontend's existing debounced autosave effect ALSO fires
  afterward (it already watches `note` state, which `onPatchApplied`
  updates) — redundant with the server's own write, but harmless
  (same field, same eventual value) and consistent with the "belt and
  suspenders" pattern already used elsewhere (`X-Accel-Buffering` header
  comment in generation.py).
- **`icd_codes` are read and written back untouched on every patch** — a
  voice edit is scoped to the four SOAP text sections; `_current_note` in
  `voice_edit.py` fetches whatever `icd_codes` currently exist (draft or
  latest saved version) and re-attaches them to the persisted `draft_note`
  unchanged. Missing this would have silently emptied the ICD chips the
  first time a voice edit ran on an encounter that had been saved (no
  `draft_note` yet) but had ICD codes on its latest version — caught before
  shipping by a dedicated test (`test_icd_codes_survive_a_voice_edit`), not
  in production.
- **`noteDirty` (Phase 7's dirty-edit flag) applies to voice edits too** —
  `onPatchApplied` in `Workspace.tsx` sets it exactly like a typed pane edit
  does. This is not a new mechanism: a voice edit is the same class of
  explicit, must-not-be-silently-overwritten clinician action Phase 7 built
  the flag to protect — if the clinician voice-edits a note and later
  starts a NEW dictation session on the same encounter, the existing guard
  correctly stops that session's auto-regeneration from clobbering the
  voice-edited content, with zero new code in `useDictation.ts` or
  `generate()`.
- **Voice editing and voice dictation are mutually exclusive in the UI,
  not at the browser-API level** — both modes use the same
  `WebSpeechTranscriptionProvider`-backed hook shape, but running dictation
  and voice-editing at once would mean two independent `SpeechRecognition`
  sessions competing for one microphone, an incoherent state no browser
  handles gracefully. `Workspace.tsx` disables "Start dictation" while
  voice-edit is active and disables "Start voice edit" while dictation is
  active or a generation is streaming — enforced in one place (button
  `disabled` props), not duplicated into either hook.
- **TTS interruption fires on `onInterim`, not `onFinal`** — waiting for a
  full utterance to finish before cancelling a stale confirmation would
  make the interrupt feel like it lags a beat behind the clinician actually
  starting to talk. Checking `speechSynthesis.speaking` and cancelling on
  the FIRST interim result (words still arriving) is what makes "just start
  talking over it" work the way a person would expect.
- **Post-ship fix: section inference for ambiguous commands defaulted to
  the wrong section** — real-microphone testing (see below) found that
  "Add that the patient has no fever" was patched into Objective instead
  of Subjective. Root cause: `VOICE_EDIT_SYSTEM` told the model the four
  valid section names but gave it no guidance on WHICH one to pick when
  the command doesn't explicitly name one — plausibly the model was
  keying off which section already contained the word "fever" (from an
  earlier test's content) rather than reasoning about what kind of
  clinical information "no fever" is. Fixed by adding an explicit
  standard-SOAP-convention rule to the prompt: patient-reported
  symptoms/complaints/denials (pain, fever, nausea, dizziness, cough, …)
  default to Subjective; measured/observed findings (vitals, exam
  findings, labs, imaging) default to Objective; an explicit section named
  in the command always overrides the default. Deliberately phrased as a
  general classification rule with illustrative examples, not a special
  case for "fever" specifically — `apply_note_patch` and `note_patch.py`
  are untouched; this is a prompt-only fix. Verified live against the REAL
  Anthropic API (scripted STT input, real model call): "Add that the
  patient has no fever" now lands in Subjective; a control case ("Add that
  blood pressure is 120 over 80") still correctly defaults to Objective,
  confirming the fix didn't overcorrect into always choosing Subjective;
  an explicit "Add to the plan that …" still honors the named section
  regardless of content type.
- **Testing**: this sandboxed environment has no microphone or speaker
  hardware, so my own end-to-end verification used a scripted mock of
  `window.SpeechRecognition`/`webkitSpeechRecognition` (same technique as
  Phase 7) plus a `speechSynthesis` spy (to observe `speak()`/`cancel()`
  calls without an audio device), driving the REAL backend, the REAL
  WebSocket connection, and the REAL Anthropic API — not a mocked LLM.
  Verified live: a spoken "add" command correctly appended a
  faithfully-transcribed clinical detail to Assessment with the confirmed
  patch and TTS message; a second, immediately-following "rewrite" command
  replaced the Plan section while every other pane stayed byte-identical
  (multiple consecutive commands, processed in order); simulating an
  active TTS utterance and then emitting new speech correctly triggered
  `cancel()` (interruption); Stop → Save created a new version (v3)
  containing both voice edits AND the preserved ICD chip, while v1 and v2
  remained byte-identical on re-fetch.
  **The user then performed the real-microphone-and-speaker validation
  this environment can't**, in Chrome on their own machine against the
  same running dev servers: add/rewrite/move/remove, ambiguous-command
  handling, content preservation, version history after edits, transcript
  editing during dictation, TTS interruption, and save-after-edits all
  confirmed working — the one gap found (section-inference defaulting to
  Objective for an unstated-section patient-reported denial) is the prompt
  fix immediately above, itself re-verified against the real API afterward
  (three cases: patient-reported → Subjective, measured vital → Objective,
  explicit section name → always honored).

### Interfaces established

- `WS /ws/encounters/{encounter_id}/voice-edit` — see API_CONTRACTS.md
  "Voice editing" for the full message contract.
- `app.note_patch.apply_note_patch(note, patch)` / `InvalidPatchError` —
  pure, the single source of truth for note mutations from a patch.
- `llm.complete_json(model, system, user_prompt, max_tokens)` — one-shot
  non-streaming completion, second call shape alongside `stream_completion`
  in the same gateway module.
- `auth.get_current_user_ws(websocket, db)` — WebSocket auth counterpart to
  `get_current_user`, returns `None` instead of raising.

---

## Phase 9 — Non-happy paths

Zero backend changes. `"Session expired"`, `"Not authenticated"`, and
`"Account deactivated"` were already distinguished by message/status since
Phase 1 (auth) and Phase 6 (deactivation); `<no_clinical_content/>` refusal
handling was already built in Phase 2. This phase is entirely about the
frontend finally consuming those existing, already-correct backend
contracts instead of the raw error just failing silently or crashing.

- **One interceptor in `api.ts`, not per-caller error handling** — every
  `api()` call transparently recovers from `"Session expired"` (pause,
  show a re-login modal, replay the exact same request, return its result
  to the original caller) with zero changes needed at any call site.
  `flushAutosave`, `saveVersion`, and any future protected call all get
  this for free. This mirrors the project's established "one gateway"
  pattern (`llm.py` for the vendor, `get_current_user` for auth
  validation) applied to the client side: recovery lives in the one place
  every request already passes through, not duplicated into every
  component that happens to call `api()`.
- **A tiny broker module (`sessionExpiry.ts`) bridges `api.ts` and
  `auth.tsx`** — `api.ts` is a plain module with no React context; the
  modal has to be rendered by something that IS a component. Rather than
  thread a re-auth callback through every caller, `api.ts` calls
  `requestReauth()` and `AuthProvider` registers the function that
  actually shows the modal, entirely decoupling "detecting the failure"
  from "presenting the recovery UI." Multiple requests expiring together
  share ONE in-flight promise (deduped in the broker) — without this, a
  autosave tick and a manual Save clicked in the same moment would each
  try to show their own modal and race two separate login attempts against
  each other.
- **`logout()` deliberately bypasses `api()`, using a raw `fetch`
  instead** — found live, not by inspection: the "Log out instead" escape
  hatch on the re-auth modal calls `logout()`, whose own
  `POST /api/auth/logout` would ALSO 401 with `"Session expired"` if the
  same stale cookie is still attached — which, going through the normal
  interceptor, would call `requestReauth()` again and reopen the very
  modal the user just tried to leave. `logout()`'s server-side call doesn't
  need to succeed for the client to consider itself logged out (it never
  did — the original code already had a bare `.catch(() => {})`), so it
  never needed the interceptor's recovery behavior in the first place; the
  fix was routing it around that behavior entirely, not adding a guard on
  top of it.
- **Deactivation is a terminal, whole-app block — not a per-component
  error state** — a deactivated account isn't a single failed request to
  retry; every subsequent call will 403 the same way. `notifyDeactivated()`
  sets one global flag in `AuthProvider` that replaces `{children}`
  entirely with a blocking screen, rather than leaving each page to decide
  how to render a 403 it happened to catch. The draft itself was never at
  risk — deactivation only ever flips `users.is_active`; `encounters` and
  `note_versions` are untouched, which is exactly why the message can
  truthfully say "your draft is preserved" rather than hedge.
- **Post-ship fix, found in live testing: deactivation mid-reauth left two
  overlapping UI states** — triggering deactivation while a "Session
  expired" modal happened to already be open (a real sequence: the
  session had also expired) meant `submitReauth`'s login attempt itself
  came back `403 Account deactivated`, which correctly set the global
  block flag — but nothing closed the now-unreachable modal underneath it,
  and its pending retry promise was left permanently unresolved (the modal
  that would have resolved it was no longer rendered, and nothing else
  could reach it). Fixed by having the deactivation handler explicitly
  close the modal and reject any pending re-auth promise at the same time
  it sets the block flag — deactivation fully pre-empts an in-flight
  session-expiry recovery rather than leaving both to render at once.
- **The demo technique for session expiry is a real, temporary
  `.env` change (`JWT_EXPIRE_MINUTES=1`), not a fake/simulated 401** — a
  corrupted or hand-edited cookie produces `"Invalid session"`
  (`jwt.InvalidTokenError`), a DIFFERENT branch in `get_current_user` than
  the one this phase's recovery flow targets (`jwt.ExpiredSignatureError`
  → `"Session expired"`). Demoing the actual code path requires an
  actually-expired-but-validly-signed token, which means either waiting
  out the real 30-minute default or shortening it for the recording.
  Documented as a one-line, fully-reversible `.env` addition in
  DEMO_FAILURES.md rather than adding any demo-only code path to the app
  itself.
- **Verified live** (not just unit-level): a real expired JWT (1-minute
  override) mid-"Save note" showed the modal with the draft still visible
  behind it; re-authenticating retried the save automatically and created
  a new version whose content — fetched back from the API afterward —
  contained the exact marker text typed before expiry, byte for byte.
  Deactivation was tested as a genuine cross-session scenario (a `curl`
  session acting as admin, completely separate from the browser's cookie
  jar, deactivating the provider mid-draft); the browser tab's very next
  autosave tick correctly rendered the blocking screen, the audit log
  showed `provider_deactivate`, and the draft's content was confirmed
  byte-identical via the admin session's own read of the same version
  afterward. No-clinical-content was re-confirmed unchanged from Phase 2:
  refusal message shown, prior SOAP content left untouched, no new version
  created.

### Interfaces established

None — this phase is entirely frontend consumption of existing backend
contracts. See API_CONTRACTS.md "Client-side recovery" for the details.

---

## Phase 10 — Test hardening, integration tests, version diff, UI polish

**Scope check before starting** (same discipline as Phases 8/9): the
kickoff message listed a server-side streaming STT stretch goal alongside
the required deliverables, but was explicit that it "should not replace or
delay" them and is only worth attempting "if substantial time remains."
Given where this project sits against its deadline after nine prior
phases, the STT stretch was consciously NOT attempted this phase — the
four required deliverables below are complete and verified; STT remains
exactly what it already was on record since Phase 7 (a swap point behind
`TranscriptionProvider`, not started).

- **Voice patch engine test suite: audited the existing Phase 8 suite
  before writing anything new** — 34 pure `apply_note_patch` tests and 16
  WS-route tests already existed. Rather than assume nothing was there
  (duplicating effort) or assume it was complete (skipping real gaps), read
  both files first and identified concrete, genuine holes: repeated-
  substring removal (does `remove` correctly touch only the FIRST match,
  as `_remove_substring`'s `replace(..., 1)` promises?), case-sensitivity
  of the verbatim-quote requirement, whitespace-stripping on `add`/
  `rewrite`, removing a section down to empty, unicode content, and —the
  most valuable addition — sequential patches, where one patch's result
  feeds the next patch's input. That last one matters because it's exactly
  how the WS route behaves in production (one command, one patch, applied
  to whatever the PREVIOUS command left behind) but wasn't covered at the
  fast, LLM-free pure-function level; only the slower WS-mocked layer
  exercised anything like it. 9 new tests added, all passing alongside the
  original 34.
- **Backend integration tests are a new file
  (`test_integration_workflow.py`), not more of the same** — every existing
  test file (by design, since Phase 1) exercises one router/feature in
  relative isolation. That's the right shape for fast, precise failure
  localization, but it means nothing had ever proven the pieces compose:
  that a note generated in one call can be voice-edited in a second, saved
  in a third, and read back correctly in a fourth; that a returning
  patient's history tool call is both audited AND visible to nothing else
  breaking; that an admin's mid-session deactivation of a provider actually
  403s that provider's NEXT call (not just blocks a fresh login, which
  `test_admin_providers.py` already covered) while leaving their draft
  exactly where they left it. Three scenarios, each crossing 3-5 routers
  in one test, same LLM-mocked/real-DB/real-TestClient approach as every
  other suite.
- **Version diff is entirely client-side — no new backend endpoint** —
  both versions being compared are already available through the existing
  `GET /api/encounters/{id}/versions/{n}`; a diff is a pure function of
  two strings the browser already has fetched, not something the backend
  needs to compute or the append-only `note_versions` table needs a new
  read path for. Word-level LCS (`frontend/src/diff.ts`, ~50 lines) rather
  than a diff library: clinical note sections are a few sentences, far too
  small to justify a dependency, and the "ask before adding a dependency"
  rule from the kickoff prompt has held for nine phases — this was a
  chance to hold it by writing 50 lines instead of asking to break it.
  Word-level (not character-level): a character diff on prose produces
  noisy, hard-to-read sub-word fragments on what are usually whole-phrase
  edits; word-level matches how a clinician actually thinks about "what
  changed." Defaults to comparing against the immediately preceding
  version — the comparison that answers "what did I just change" without
  any clicks — with a dropdown to pick any other saved version instead.
- **UI polish: two concrete, bounded fixes, not an open-ended pass** —
  (1) version-history rows were `onClick`-only `<tr>` elements with no
  keyboard path to them at all; added `tabIndex`, `role="button"`,
  `aria-label`, and Enter/Space handling. (2) the version viewer modal had
  no Escape-to-close, a near-universal modal convention; added a
  window-level keydown listener scoped to the modal's own mount lifetime.
  Found by deliberately checking, not by guessing: a mobile-width
  (375px) screenshot of the workspace header showed the "Save note" button
  genuinely clipped off the right edge of the viewport (`flex
  justify-between` with no wrap point and no width slack at small sizes).
  Fixed with `flex-wrap` on the header and its two inner groups, tighter
  mobile padding, and hiding the DOB line below the `sm` breakpoint (the
  least load-bearing piece of header text, freeing width for the
  status badge and Save button that always matter). Verified at both
  375px and desktop width post-fix — no clipping, no desktop regression.

### Interfaces established

- `frontend/src/diff.ts` — `wordDiff(oldText, newText): DiffToken[]`, pure,
  no backend or dependency involvement.
