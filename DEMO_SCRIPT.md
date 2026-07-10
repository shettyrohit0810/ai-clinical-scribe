# Demo script — full walkthrough

A practical, in-order script covering every implemented feature with real
seeded data. Non-happy-path scenarios (session expiry, deactivation,
no-clinical-content) are a **separate, already-complete script** —
[DEMO_FAILURES.md](DEMO_FAILURES.md) — not repeated here; scene 12 below is
just the handoff point.

Rough total: 8–10 minutes for the full happy path at a normal narrating
pace. Each scene notes what to click, what to say, and what result proves
the feature actually works — not just that a button was clicked.

## Before recording

- [ ] Backend running (`uvicorn app.main:app --port 8001`), frontend
      running (`npm run dev`), both against a freshly-seeded DB
      (`python -m app.seed` **and** `python -m app.seed_icd` — both
      idempotent, safe to re-run; without the second, notes generate with
      no ICD codes and the search widget returns nothing).
- [ ] **Chrome**, not another browser — dictation and voice editing need
      Web Speech API support (see README "Browser support").
- [ ] Microphone permission will be prompted the first time you click
      Start dictation/Start voice edit — accept it before recording, or
      the OS permission dialog will be in the recording.
- [ ] Two browser windows/profiles ready if you're also recording
      DEMO_FAILURES.md's deactivation scenario in the same session (one
      provider, one admin — separate cookie jars).

---

## 1. Login (~15s)

Go to `http://localhost:5173`. Sign in as `sarah.chen@clinic.example` /
`ScribeDemo1!`.

*Say:* JWT in an httpOnly cookie — never touches JavaScript. Provider data
is isolated server-side; a provider only ever sees their own encounters.

## 2. Dashboard (~10s)

Land on the encounter list. Point out it's provider-scoped (Dr. Chen sees
only her own prior encounters) and note the **New Encounter** action.

## 3. New encounter, brand-new patient (~60s)

Click **New Encounter**. Enter a genuinely new identity (not a seeded
patient) — first/last name + DOB. Submit.

*Say:* patient matching is case-insensitive on (first, last, DOB); a new
identity creates a new patient row. No "returning patient" banner here —
this is the clean new-patient path.

Paste or type a short transcript, e.g.:
> *"Patient presents with sore throat and low-grade fever for two days.
> No cough. Exam shows mild pharyngeal erythema, no exudate. Vitals
> stable."*

Click **Generate note**. Narrate while it streams:

*Say:* SOAP panes fill progressively over Server-Sent Events, not after a
spinner — watch Subjective, Objective, Assessment, Plan fill in as tokens
arrive. ICD-10 codes at the bottom are chosen ONLY from a locally-retrieved
candidate list (cosine similarity over a 289-code catalog) — the model
cannot hallucinate a code that isn't in that list.

## 4. Template effect (~30s)

Go back to Template, pick **Urgent Care**. Click **Generate note** again.

*Say:* watch the Plan section — Urgent Care's template instructs a
`RETURN PRECAUTIONS:` line in caps at the end, and much shorter sections
overall (max two sentences each). This is admin-authored template text
interpolated into the prompt in a clearly-delimited, untrusted block — it
can style the note but can never override the system's clinical-accuracy
rules.

## 5. Manual edit + save (~20s)

Click into the Assessment pane and type an addition by hand. Click
**Save note**.

*Say:* this creates version 1 — saves are append-only; nothing is ever
updated or deleted in the version history table.

## 6. Returning patient + history injection (~60s)

Click **New Encounter** again. Enter **Margaret Thompson**, DOB
**1954-03-17** (seeded — has 3 prior saved encounters across two
providers).

*Say:* watch for the "Returning patient" banner citing prior encounter
count.

Add a short follow-up transcript, e.g. *"Margaret here for knee follow-up,
pain improved."* Click **Generate note**.

*Say:* watch for the "History referenced: N prior encounters" indicator —
the model called a zero-argument `fetch_patient_history` tool mid-generation.
The tool takes no arguments on purpose: the backend decides which patient's
history to fetch from the encounter being generated, never from anything
the model outputs — there's no parameter for a prompt injection to target.
Every invocation is also written to the audit log.

## 7. ICD-10 search widget (~20s)

In the search box, type **"knee pain"**. Click a result.

*Say:* this is a second, direct caller of the same local embed+cosine
function generation uses internally — but this is the clinician's own
free-text annotation appended to Assessment, distinct from the
model-selected, candidate-constrained chips above it.

## 8. Voice dictation (~90s — needs a real microphone)

Click **Start dictation**. Speak a few sentences with a natural pause
partway through, e.g.:; *"Patient reports headache for three days, worse
with light."* (pause 2-3 seconds) *"No fever, no neck stiffness."*

*Say while it's running:* watch the transcript fill live, and watch the
SOAP panes auto-update on the pause — that's a Haiku-tier rolling draft,
triggered after ~2s of silence or every 6s of continuous speech, whichever
comes first. Type a manual correction into the transcript right now, while
still listening — it's never blocked, and dictation appends after it, not
over it.

Click **Stop**.

*Say:* Stop triggers one Sonnet-tier final generation — the quality tier,
used once, versus Haiku's speed used continuously during dictation.

## 9. Conversational voice editing (~90s — needs a real microphone + speakers)

Click **Start voice edit**. Speak a command that doesn't name a section,
e.g. *"Add that the patient denies vision changes."*

*Say:* listen for the spoken confirmation — that's a real requirement:
patient-reported findings default to Subjective per standard SOAP
convention, unless the command names a section explicitly.

Speak a second command immediately, e.g. *"Move the vision changes note to
objective."* — demonstrates a `move` op and that consecutive commands
process in order.

*Say:* every command becomes exactly one structured JSON patch — never a
regenerated note — applied through one function that's unit-tested
independently of the model. `remove`/`move` require the model to quote
existing text verbatim; an inexact quote fails validation rather than
silently rewriting the wrong thing.

Optional: while the confirmation is being spoken back, start talking again
to demonstrate interruption — the voice should cut off immediately rather
than finish the sentence.

Click **Stop**, then **Save note**.

## 10. Version history + diff (~30s)

Scroll to Version History. Click the newest version, then click the
previous version in the "Compare to" dropdown (it defaults there already).

*Say:* word-level diff, entirely client-side — additions in green,
removals struck through in red — computed from the same
`GET .../versions/{n}` call the version viewer already made, no new
backend endpoint.

## 11. Admin dashboard (~60s)

Log out, log in as `admin@clinic.example` / `ScribeDemo1!`. Go to Admin.

- **Encounters tab** — filter by provider and date range; point out this
  is the SAME endpoint providers use, just with admin-only query params.
- **Providers tab** — create a provider live, or point at the seeded
  three; mention deactivate/reactivate (full demo is in DEMO_FAILURES.md).
- **Templates tab** — edit a template's instructions, save, then go
  generate a note on any encounter using that template: the new
  instructions apply immediately, no cache to bust, no refresh needed.
- **Audit tab** — show the audit trail: encounter creates, saves, the
  tool-call from scene 6, the template edit you just made — every mutation
  in the system writes a row here in the same transaction as the action.

## 12. Non-happy paths (handoff)

*Say:* three failure scenarios — a no-clinical-content refusal, session
expiry with transparent recovery, and account deactivation — are each
demoed in under 90 seconds in DEMO_FAILURES.md, with exact setup steps
since two of them need either a real token expiry or a second admin
session to trigger honestly rather than faked.

## Closing

*Say:* every feature shown reused existing infrastructure rather than
building parallel paths — dictation and voice editing both drive the same
SSE/patch mechanisms other features already established, non-happy-path
recovery is one interceptor every request already passes through, and
version diffing needed no new backend endpoint at all. Full rationale for
every one of these choices is in DECISIONS.md.
