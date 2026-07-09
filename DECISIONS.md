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
