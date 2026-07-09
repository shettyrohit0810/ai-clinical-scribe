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
