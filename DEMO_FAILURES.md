# Demo: non-happy paths

Three failure scenarios, each demoable in under 90 seconds. All three are
implemented and covered live below — no code changes needed to run any of
them; scenarios 2 and 3 need a small, fully-reversible setup step first
(detailed in each section).

---

## 1. No-clinical-content transcript (~20s)

**What it proves:** the model's own refusal (`<no_clinical_content/>`) is
handled without heuristics on the backend, and the UI shows a calm state —
no note generated, nothing saved, existing content untouched.

**Steps:**
1. Open any encounter's workspace.
2. Type or paste a transcript with no clinical content, e.g. *"We talked
   about the weather and how the Yankees did last night."*
3. Click **Generate note**.

**Expected result:** "No clinical content detected in this transcript.
Nothing was generated or saved." appears below the Generate button. Any
SOAP content already on screen from before is untouched (Phase 7's
deferred-clear design — panes are never blanked until real content is
about to replace them). No new version appears in Version History.

No setup or cleanup required — this is stateless.

---

## 2. Session expiry (~75s, mostly waiting)

**What it proves:** a 401 mid-action doesn't lose the draft — the physician
re-authenticates in place and the exact same request is retried
automatically, with zero manual re-entry of anything.

**Setup (one-time, reversible):** the real JWT expiry is 30 minutes, too
long to sit and wait for on camera. Temporarily shorten it:

```bash
# backend/.env — add this line
echo "JWT_EXPIRE_MINUTES=1" >> backend/.env
```

Restart the backend (it has no --reload; this is what picks up the new
`.env` value):
```bash
# however you normally run it, e.g.:
cd backend && .venv/bin/uvicorn app.main:app --port 8001
```

**Steps:**
1. Log in and open an encounter with a draft. Type something distinctive
   into a SOAP pane so it's obvious the same content survives (e.g. add
   "test marker" to the Plan).
2. Wait about 65 seconds (the token is now 1 minute; give it a few seconds
   of margin) — do nothing else in the tab.
3. Click **Save note**.

**Expected result:** a "Session expired" modal appears immediately — the
page underneath is untouched, your typed content still visible behind the
modal. Enter the same password and click **Sign in and retry**. The modal
closes, the save completes on its own (no need to click Save note again),
and a new version appears in Version History containing your test marker
text. Nothing was lost.

**Cleanup:** remove the line you added to `backend/.env` and restart the
backend to restore the normal 30-minute expiry.

*(Under the hood: every request goes through one shared `api()` wrapper —
see `frontend/src/api.ts` and `frontend/src/sessionExpiry.ts` — so this
same recovery applies to autosave, save, and every other authenticated
call, not just the one demoed here.)*

---

## 3. Provider deactivation (~30s)

**What it proves:** deactivating an account mid-session doesn't corrupt or
lose that provider's in-progress draft — it's a soft flag, and the
workspace fails safe with a clear message instead of a broken/blank screen.

**Steps (two browser sessions — e.g. one normal window, one
incognito/private, so the two logins don't share a cookie jar):**
1. **Window A:** log in as a provider (`sarah.chen@clinic.example` /
   `ScribeDemo1!`) and open an encounter with a draft note.
2. **Window B:** log in as admin (`admin@clinic.example` /
   `ScribeDemo1!`), go to the admin dashboard's Providers tab, and click
   **Deactivate** on Dr. Sarah Chen.
3. Switch back to **Window A** and wait a few seconds (the next autosave
   tick fires within 3s), or click anything that triggers a request (e.g.
   type in the transcript).

**Expected result:** Window A's entire screen is replaced with "Account
deactivated — Your draft is preserved. Contact your administrator to
restore access…" — no broken UI, no silent failure. The draft is untouched
in the database (verifiable via the admin session: `GET
/api/encounters/{id}/versions/{n}` still returns the exact content).

**Cleanup:** in Window B, click **Activate** on Dr. Sarah Chen. Window A
needs a page reload to sign back in (the blocking screen says so) — this
is deliberate: deactivation is treated as terminal for the current session,
not something to silently paper over and keep going.
