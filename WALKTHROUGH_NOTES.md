# Walkthrough notes

Talking points for the technical walkthrough, organized by theme rather
than phase (DECISIONS.md is the phase-by-phase log; this is the "if asked
about X" reference). Every point here traces to a real design decision or
a real bug found during live testing — nothing here is aspirational.

## Infrastructure & resilience

- **One deployable unit.** nginx serves the built SPA and reverse-proxies
  both `/api` (JSON + SSE, `proxy_buffering off`) and `/ws` (WebSocket
  voice editing, Upgrade/Connection headers) to gunicorn on
  `127.0.0.1:8001`. The `/ws` split from `/api` was reserved in the nginx
  config since Phase 0, long before voice editing existed — worth
  mentioning as evidence of infra-first sequencing (Phase 0's whole goal
  was proving SSE-through-nginx before any product code existed).
- **DB connection pool** (`app/db.py`): `pool_size=10, max_overflow=5,
  pool_pre_ping=True`. Two gunicorn workers × (10+5) = 30 connections max,
  comfortably under a small RDS instance's ~100 connection ceiling with
  headroom for Alembic and an operator psql session. `pre_ping` means a
  connection silently dropped by an RDS failover is replaced transparently
  instead of handing the first unlucky request a 500.
- **Retry strategy is SDK-native, not hand-rolled.** `max_retries=1` on the
  Anthropic client delegates "one retry with backoff on transient
  failures" to the vendor SDK (retries connection errors, 408/429, 5xx
  with exponential backoff) — leverages the platform instead of owning
  retry-logic bugs. Anything that survives the retry becomes one
  structured `("error", message)` event; callers never see an exception.
- **One LLM gateway module (`app/llm.py`), two call shapes.**
  `stream_completion`/`stream_note_generation` for progressive SOAP text;
  `complete_json` (Phase 8) for one-shot structured patches. Same client
  singleton, same never-raises resilience contract, same
  timeout/max_tokens cap/call-counter logging — one place every property
  applies uniformly, not duplicated per caller.

## Core generation design

- **Tagged XML output, not JSON.** The model emits
  `<subjective>…</subjective>` etc. because the stream needs incremental
  parsing: a partial tag is trivially detectable (hold back at most
  `len(tag)-1` chars), while a partial JSON string is unparseable until
  the whole document closes — which would force buffer-then-dump and kill
  progressive rendering. Tags also fail soft: a malformed section damages
  one pane, not the whole note. The parser is pure and fuzz-tested
  char-by-char, including tags deliberately split across chunk
  boundaries.
- **Two-tier models are one decision, reused three times.** Haiku for
  latency-sensitive/high-frequency work (rolling dictation drafts), Sonnet
  for quality-weighted/low-frequency work (final notes, voice-edit
  patches). Not three separate choices — `llm.py`'s docstring committed to
  the voice-edit tier back in Phase 2, five phases before voice editing
  existed.
- **Candidate-constrained ICD selection.** The model chooses only from a
  list the backend retrieved (local hashed bag-of-words cosine over a
  289-code catalog) — codes cannot be hallucinated because there's no path
  to output one that wasn't in the candidate list. Same philosophy reused
  in Phase 8 for voice-edit `remove`/`move`: the model must quote existing
  note text verbatim rather than describe it, so `apply_note_patch` can
  validate the quote against real content before mutating anything.
- **`fetch_patient_history` takes zero arguments.** The backend resolves
  "which patient" from the encounter being generated — never from model
  output. Any parameter (name, id) would make patient selection a model
  decision requiring authorization validation: a prompt-injection surface.
  Zero arguments means containment by construction, not by validation, and
  the audit log entry is exactly the query that ran (nothing to fake).
- **Template instructions are untrusted input, syntactically marked as
  such.** Admin-authored template text is interpolated inside a
  clearly-delimited, quoted frame in the user turn — never the system
  prompt — with an explicit note that it styles output but cannot override
  safety rules. The column is named `instructions`, deliberately not
  `system_prompt`, as a naming-level reminder of the trust boundary.

## Data model

- **Schema designed once, phases add rows not tables.** All 7 tables
  landed in one migration in Phase 1 even though data arrives per phase.
- **Every index has an inline rationale comment** (`models.py`'s own
  framing: "the ERD walkthrough answers"). Highlights: `users.email`'s
  UNIQUE constraint doubles as the login-lookup index (no separate index
  needed); `patients` has a composite UNIQUE on (first, last, dob) that
  *is* the returning-patient matcher, plus a last-name-first lookup index
  matching clinical search convention; `encounters` has a composite
  `(provider_id, created_at DESC)` index serving the one hot dashboard
  query in a single scan; `note_versions`' UNIQUE on
  `(encounter_id, version_number)` doubles as the append-only
  history-retrieval index — no second index needed for the same columns.
- **`draft_note` (JSONB scratch) vs `note_versions` (append-only
  history)** — two different mutability contracts on purpose. A draft is
  mutable workspace state that autosaves every ~3s; a version is an
  immutable append-only row inserted on explicit Save, proven by an
  exact-value re-read test (not just a row count — row count alone can't
  distinguish append-only from update-in-place).
- **Isolation returns 404, not 403.** A 403 would confirm a foreign
  encounter id exists; 404 leaks nothing. Same pattern reused for the
  WebSocket voice-edit route (`websocket.close()` without detail, the
  nearest WS-native equivalent).
- **No pgvector, deliberately, at this scale.** ICD embeddings are JSONB
  float arrays with Python cosine over ~300 rows (microseconds). Anthropic
  has no embeddings endpoint, and a second vendor means a second API key,
  spend ceiling, and failure mode for a problem token-overlap already
  solves at this catalog size. Revisit only past roughly 10k rows — the
  swap is a one-function change (`embed_text`) plus a re-seed, not a
  rewrite.

## Voice dictation vs. voice editing — two different problems

These get confused if not stated explicitly: **dictation produces new
transcript text and regenerates a whole note; voice editing patches an
already-generated note.** Different transport (SSE vs. persistent
WebSocket), different LLM call shape (streaming multi-paragraph vs.
one-shot small JSON), different browser-API usage mode of the same
`TranscriptionProvider` interface (buffer-append vs. one-utterance-one-
command), mutually exclusive in the UI because they're two incompatible
ways to use one microphone.

**Dictation:**
- Self-timed 1s poll (2s pause OR 6s continuous-speech, whichever first)
  rather than the recognizer's own `onspeechend` — that event's firing
  semantics differ too much across Chrome/Safari in continuous mode to be
  a reliable trigger.
- Transcript is one editable buffer; `onFinal` reads it fresh at commit
  time (not a value captured at dictation-start), so manual edits made
  mid-session land correctly and the next spoken chunk appends after them,
  not over them.
- `noteDirty` guard applies uniformly to BOTH the rolling-draft trigger
  AND the stop-triggered final trigger — a physician's edit always wins
  over auto-regeneration regardless of which tier would have fired.

**Voice editing:**
- Patch, never regenerate — the central constraint. One JSON patch
  (`add`/`remove`/`rewrite`/`move`) per command, applied through exactly
  one function (`apply_note_patch`) that's pure and unit-tested
  independent of any LLM call.
- `{"op": "unclear"}` is the model's own escape hatch for ambiguous
  commands — mirrors `<no_clinical_content/>` from note generation.
- TTS interruption fires on the FIRST interim speech result, not after a
  full utterance — checked deliberately so "just start talking over it"
  feels immediate rather than a beat behind.

**Two real bugs found only through live testing** (worth mentioning as
process, not just outcome): a stale-closure bug where dictation's
rolling-regen timer could act on outdated state (fixed with a
latest-ref pattern); and a section-inference bug where an unstated-section
voice command ("add that the patient has no fever") defaulted to Objective
instead of Subjective — fixed with an explicit SOAP-convention rule in the
prompt, re-verified against the real API with a control case to confirm
the fix didn't overcorrect.

## Non-happy-path recovery

- **One interceptor (`api.ts`), not per-component error handling.** Every
  request already passes through one wrapper; session-expiry recovery
  (pause → show re-login modal → replay the exact failed request → return
  its result) lives there once, so `flushAutosave`, `saveVersion`, and any
  future protected call get it for free.
- **Recoverable vs. terminal is a real distinction, not just two error
  messages.** Session expiry is recovered by re-authenticating and
  retrying. Deactivation is NOT retried — re-authenticating can't fix a
  deactivated account, so it's treated as terminal: one global flag
  replaces the entire app with a blocking screen instead of leaving each
  page to improvise a response to a 403 it happened to catch.
- **A React-free broker module bridges a plain fetch wrapper and a React
  component** (`sessionExpiry.ts`) — `api.ts` has no component to render a
  modal from, so it calls a registered function and lets `AuthProvider`
  own the actual UI. Concurrent requests expiring together share one
  in-flight promise so only one modal ever shows.
- **Found live: two bugs a code read wouldn't have caught.** The re-auth
  modal's own "Log out instead" escape hatch called `logout()`, which
  itself went through the same interceptor — a still-stale cookie could
  make the logout call ALSO 401, reopening the very modal being escaped
  (fixed by routing `logout()` around the interceptor with a raw fetch).
  And deactivation detected while a re-auth modal was already open left
  that modal's promise permanently unresolved once the block screen made
  it unreachable (fixed by having deactivation explicitly close and reject
  it).

## Testing strategy

- **LLM calls are always mocked; everything else is real.** Every test
  file mocks `stream_completion`/`stream_note_generation`/`complete_json`
  at the exact seam the route uses — deterministic, fast, free — while
  exercising the real FastAPI app, the real test Postgres database, and
  the real `TestClient` (including WebSocket tests via
  `client.websocket_connect`).
- **One feature per test file, plus a separate integration suite that
  crosses several.** Every router/feature has its own file for fast,
  precise failure localization. `test_integration_workflow.py` (Phase 10)
  is different on purpose: three scenarios that each cross 3-5 routers in
  a single test — full encounter lifecycle including a voice-edit
  mid-flow, returning-patient history with admin visibility and audit
  ordering, and deactivation mid-session as an automated test (not just a
  fresh-login check).
- **Pure functions get pure tests.** `apply_note_patch` and
  `TaggedStreamParser` are both I/O-free by design specifically so their
  trickiest cases (char-by-char tag splitting, sequential chained patches,
  case-sensitive verbatim matching, repeated-substring removal) are fast
  deterministic unit tests, not slow round-trips through a mocked LLM.
- **136 backend tests, 0 skipped, checked after every phase.** No frontend
  unit-test framework was added; frontend correctness was verified via
  `tsc --noEmit` plus live browser verification for every UI-facing
  change.
- **Live verification methodology for voice features**: this sandboxed
  development environment has no microphone or speaker hardware. Every
  voice feature was verified end-to-end by scripting a mock of the
  browser's `SpeechRecognition`/`speechSynthesis` event contract
  (`onresult`/`onerror`/`onend`, `speak()`/`cancel()`) while leaving the
  backend and the Anthropic API entirely real — never a mocked LLM. The
  developer separately performed real-hardware verification and reported
  results (see DECISIONS.md Phase 8 for the one real bug that surfaced
  only under real speech).

## Process notes worth mentioning

- **Scope-check discipline before Phases 8, 9, and 10**: each kickoff
  message was checked against the actual codebase (grep + a code-graph
  search) before writing anything, rather than trusting a description of
  "existing" infrastructure at face value. Phase 8 found neither "the
  existing WebSocket endpoint" nor `apply_note_patch` actually existed yet
  — both were advance-reserved (nginx config, a docstring) but never
  built. Documented transparently in DECISIONS.md rather than silently
  building as if the premise were already true.
- **Every post-ship bug fix is documented with root cause, fix, and
  re-verification** — not just "fixed a bug." DECISIONS.md has the full
  list; the pattern throughout is: found live (not by code review alone),
  root-caused to one specific wrong assumption, fixed at the source rather
  than patched at the symptom, re-verified against the real system
  afterward.
