import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  ApiError,
  api,
  type DraftNote,
  type EncounterDetail,
  type IcdCode,
  type NoteVersion,
  type NoteVersionSummary,
  type Template,
} from "../api";
import { useDictation } from "../useDictation";
import { useVoiceEdit } from "../useVoiceEdit";

// Debounce delay for the ICD search-as-you-type box — short enough to feel
// live, long enough that "knee pain" doesn't fire five separate requests.
const ICD_SEARCH_DEBOUNCE_MS = 300;

const SECTIONS = ["subjective", "objective", "assessment", "plan"] as const;
type SectionName = (typeof SECTIONS)[number];
type NoteText = Record<SectionName, string>;

const EMPTY_NOTE: NoteText = { subjective: "", objective: "", assessment: "", plan: "" };

type GenState = "idle" | "streaming" | "done" | "empty" | "error";
type SaveState = "clean" | "dirty" | "saving" | "saved";

/**
 * The encounter workspace: transcript on the left, four SOAP panes on the
 * right filling progressively during generation.
 *
 * Persistence design (walkthrough): every edit marks the workspace dirty; a
 * 3s-debounced PATCH autosaves transcript + draft note onto the encounter
 * row. That row IS the session — refresh or switch devices and this screen
 * rehydrates from GET /encounters/{id} (draft_note wins over the last saved
 * version). Generate flushes the autosave first, then opens the SSE stream,
 * so the server always generates from the freshest transcript.
 */
export default function Workspace() {
  const { id } = useParams();
  const location = useLocation();
  const routeState = location.state as
    | { returning?: boolean; priorEncounters?: number }
    | null;

  const [detail, setDetail] = useState<EncounterDetail | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [transcript, setTranscript] = useState("");
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [note, setNote] = useState<NoteText>(EMPTY_NOTE);
  const [icdCodes, setIcdCodes] = useState<IcdCode[]>([]);
  const [gen, setGen] = useState<GenState>("idle");
  const [genError, setGenError] = useState("");
  // Set when the model called fetch_patient_history during this generation.
  const [historyReferenced, setHistoryReferenced] = useState<number | null>(null);
  // True whenever the physician has hand-edited a SOAP pane since the last
  // completed generation. Automatic (dictation-triggered) regenerations
  // check this and skip themselves rather than overwrite an edit the
  // physician never asked to have replaced — see generate() below. The
  // manual "Generate note" button ignores this flag: an explicit click is,
  // by definition, not an "unexpected" overwrite.
  const [noteDirty, setNoteDirty] = useState(false);
  // Distinguishes an auto (dictation) generation from a manual one, purely
  // for UI messaging — both share the exact same generate() call and SSE
  // wiring underneath.
  const [autoGenerating, setAutoGenerating] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("clean");
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showBanner, setShowBanner] = useState(routeState?.returning ?? false);

  // ICD-10 search widget — separate from `icdCodes` (the model-selected,
  // candidate-constrained chips): search results are the clinician's own
  // pick, appended as free text into Assessment, not into that chip array.
  const [icdQuery, setIcdQuery] = useState("");
  const [icdResults, setIcdResults] = useState<IcdCode[]>([]);
  const [icdSearching, setIcdSearching] = useState(false);

  // Version history: summaries for the panel, full content only when viewing.
  const [versions, setVersions] = useState<NoteVersionSummary[]>([]);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<NoteVersion | null>(null);
  const [viewingError, setViewingError] = useState(false);

  const sourceRef = useRef<EventSource | null>(null);
  const loadedRef = useRef(false);
  // Concurrency guard for automatic generations: React state (`gen`) can be
  // stale inside the dictation timer's closure, so an in-flight flag needs
  // to live in a ref that's always read synchronously and up to date.
  const genInFlightRef = useRef(false);

  const refreshVersions = useCallback(() => {
    api<NoteVersionSummary[]>(`/api/encounters/${id}/versions`)
      .then(setVersions)
      .catch(() => {});
  }, [id]);

  // ---- load + hydrate --------------------------------------------------
  useEffect(() => {
    api<Template[]>("/api/templates").then(setTemplates).catch(() => {});
    api<EncounterDetail>(`/api/encounters/${id}`).then((d) => {
      setDetail(d);
      setTranscript(d.transcript);
      setTemplateId(d.template_id);
      // Unsaved workspace state wins over the last saved version.
      const src = d.draft_note ?? d.latest_version;
      if (src) {
        setNote({
          subjective: src.subjective,
          objective: src.objective,
          assessment: src.assessment,
          plan: src.plan,
        });
        setIcdCodes(src.icd_codes ?? []);
      }
      if (d.latest_version) setSavedVersion(d.latest_version.version_number);
      loadedRef.current = true;
    });
    refreshVersions();
    return () => sourceRef.current?.close();
  }, [id, refreshVersions]);

  async function viewVersion(versionNumber: number) {
    setViewerOpen(true);
    setViewingError(false);
    setViewingVersion(null); // shows a loading state until the fetch resolves
    try {
      const v = await api<NoteVersion>(
        `/api/encounters/${id}/versions/${versionNumber}`,
      );
      setViewingVersion(v);
    } catch {
      setViewingError(true);
    }
  }

  // ---- ICD-10 search widget (debounced ~300ms) --------------------------
  useEffect(() => {
    const q = icdQuery.trim();
    if (q.length < 2) {
      setIcdResults([]);
      setIcdSearching(false);
      return;
    }
    setIcdSearching(true);
    const timer = setTimeout(() => {
      api<IcdCode[]>(`/api/icd/search?q=${encodeURIComponent(q)}`)
        .then(setIcdResults)
        .catch(() => setIcdResults([]))
        .finally(() => setIcdSearching(false));
    }, ICD_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [icdQuery]);

  function appendIcdToAssessment(c: IcdCode) {
    setNote((prev) => ({
      ...prev,
      assessment: prev.assessment
        ? `${prev.assessment}\n${c.code}: ${c.description}`
        : `${c.code}: ${c.description}`,
    }));
  }

  // ---- autosave (debounced ~3s) ----------------------------------------
  const flushAutosave = useCallback(async () => {
    const draft: DraftNote = { ...note, icd_codes: icdCodes };
    setSaveState("saving");
    try {
      await api(`/api/encounters/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          transcript,
          template_id: templateId,
          draft_note: draft,
        }),
      });
      setSaveState("saved");
    } catch {
      setSaveState("dirty"); // retried by the next edit's debounce tick
    }
  }, [id, transcript, templateId, note, icdCodes]);

  useEffect(() => {
    if (!loadedRef.current) return; // don't autosave the initial hydration
    setSaveState("dirty");
    const timer = setTimeout(flushAutosave, 3000);
    return () => clearTimeout(timer);
  }, [transcript, templateId, note, icdCodes, flushAutosave]);

  // ---- generation over SSE ---------------------------------------------
  // The SAME endpoint and event wiring serves three callers: the manual
  // "Generate note" button (tier=final, auto=false — the pre-Phase-7
  // behavior, unchanged), and dictation's rolling haiku-tier regeneration
  // and its one final sonnet-tier pass on Stop (both tier/auto set by
  // useDictation's callbacks below). Phase 7 adds parameters and two
  // safety guards; it does not add a second SSE implementation.
  const generate = useCallback(
    async (opts: { tier?: "final" | "draft"; auto?: boolean } = {}) => {
      const { tier = "final", auto = false } = opts;
      if (auto) {
        // One stream at a time — an automatic trigger that fires while a
        // previous one is still running just skips; the next timer tick
        // (or the eventual Stop-dictation final pass) will try again.
        if (genInFlightRef.current) return;
        // The core Phase 7 safety rule: a physician's own edit always wins
        // over an automatic regeneration. Only an explicit button click
        // may overwrite dirty note state.
        if (noteDirty) return;
      }

      sourceRef.current?.close();
      await flushAutosave(); // server generates from the freshest transcript
      genInFlightRef.current = true;
      setAutoGenerating(auto);
      setHistoryReferenced(null);
      setGen("streaming");

      // Deferred clear: panes are NOT blanked until the model actually
      // starts producing SOAP content. A refusal (<no_clinical_content/>)
      // or a transient failure therefore leaves whatever was already
      // showing completely untouched — required for rolling regeneration
      // to run silently in the background without risking a "the note
      // just vanished" moment, and a strict improvement over blanking
      // eagerly for the manual button too.
      let cleared = false;
      const ensureCleared = () => {
        if (cleared) return;
        cleared = true;
        setNote(EMPTY_NOTE);
        setIcdCodes([]);
      };

      const qs = tier === "draft" ? "?tier=draft" : "";
      const source = new EventSource(`/api/encounters/${id}/generate${qs}`);
      sourceRef.current = source;
      // Server-side fetch_patient_history tool ran (returning patients only).
      source.addEventListener("history", (e) => {
        setHistoryReferenced(JSON.parse((e as MessageEvent).data).prior_encounters);
      });
      // Model emitted text before its tool call — restart the panes.
      source.addEventListener("reset", () => {
        cleared = true;
        setNote(EMPTY_NOTE);
        setIcdCodes([]);
      });
      source.addEventListener("section", (e) => {
        ensureCleared();
        const { section, delta } = JSON.parse((e as MessageEvent).data);
        setNote((prev) => ({ ...prev, [section]: prev[section as SectionName] + delta }));
      });
      source.addEventListener("icd_codes", (e) => {
        ensureCleared();
        setIcdCodes(JSON.parse((e as MessageEvent).data));
      });
      source.addEventListener("no_clinical_content", () => {
        source.close();
        genInFlightRef.current = false;
        // Auto: nothing was cleared, nothing to explain — stay quiet and
        // let the next dictation trigger try again. Manual: the physician
        // asked explicitly and deserves the existing banner.
        if (!auto) setGen("empty");
        else setGen("done");
      });
      source.addEventListener("error", (e) => {
        source.close();
        genInFlightRef.current = false;
        const data = (e as MessageEvent).data;
        const message = data ? JSON.parse(data).message : "Connection lost — try again.";
        if (!auto) {
          setGenError(message);
          setGen("error");
        } else {
          setGen("done");
        }
      });
      source.addEventListener("done", () => {
        source.close();
        genInFlightRef.current = false;
        setGen("done");
        // Only clear "dirty" if this stream actually produced fresh
        // content — a refusal/failure must NOT silently re-arm auto
        // regeneration to overwrite edits that were never superseded.
        if (cleared) setNoteDirty(false);
      });
      // EventSource network failure (no server event) also lands here:
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) return;
        source.close();
        genInFlightRef.current = false;
        if (!auto) {
          setGenError("Connection lost — your transcript is safe. Try again.");
          setGen("error");
        } else {
          setGen("done");
        }
      };
    },
    [id, noteDirty, flushAutosave],
  );

  // ---- voice dictation (Web Speech API via TranscriptionProvider) -------
  const dictation = useDictation({
    getTranscript: useCallback(() => transcript, [transcript]),
    onCommitTranscript: useCallback((next: string) => setTranscript(next), []),
    onRollingRegenerate: useCallback(() => generate({ tier: "draft", auto: true }), [generate]),
    onFinalRegenerate: useCallback(() => generate({ tier: "final", auto: true }), [generate]),
  });

  // ---- voice editing (Web Speech API command mode + WebSocket) ----------
  // A conversational patch, not a regeneration: each spoken command is one
  // WS round trip, applied server-side ONLY through apply_note_patch (never
  // written here directly). Marks the note dirty exactly like a typed pane
  // edit does — a voice edit is the same class of explicit, must-not-be-
  // silently-overwritten change the Phase 7 dirty guard already protects
  // against a LATER dictation session's auto-regeneration.
  const voiceEdit = useVoiceEdit({
    encounterId: id!,
    onPatchApplied: useCallback((patchedNote) => {
      setNote((prev) => ({ ...prev, ...patchedNote }));
      setNoteDirty(true);
    }, []),
  });

  // ---- save as version ---------------------------------------------------
  // Errors are caught (not left to reject silently) because api() may now
  // pause here for a session-expiry re-login (Phase 9) — if that flow is
  // cancelled, or a genuinely different failure happens, the physician
  // needs to see it rather than watch "Save note" do nothing. Nothing in
  // this function needs to know about re-auth: api() already retried the
  // exact same request transparently before this catch could ever see the
  // session-expiry case at all.
  async function saveVersion() {
    setSaveError(null);
    try {
      await flushAutosave();
      const result = await api<{ version_number: number }>(
        `/api/encounters/${id}/save`,
        { method: "POST", body: JSON.stringify({ ...note, icd_codes: icdCodes }) },
      );
      setSavedVersion(result.version_number);
      setDetail((d) => (d ? { ...d, status: "saved" } : d));
      refreshVersions();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Could not save — try again.");
    }
  }

  if (!detail) {
    return (
      <div className="mx-auto max-w-6xl space-y-3 px-6 py-8">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-16 animate-pulse rounded bg-slate-200" />
        ))}
      </div>
    );
  }

  const p = detail.patient;
  const noteIsEmpty = SECTIONS.every((s) => !note[s]);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <Link to="/" className="text-xs text-blue-700 hover:underline">
              ← Encounters
            </Link>
            <h1 className="text-sm font-semibold text-slate-900">
              {p.last_name}, {p.first_name}
            </h1>
            <span className="text-xs text-slate-500">DOB {p.dob}</span>
            <span
              className={
                detail.status === "saved"
                  ? "rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700"
                  : "rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700"
              }
            >
              {detail.status}
              {savedVersion ? ` · v${savedVersion}` : ""}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {saveError && (
              <span role="alert" className="text-xs text-red-700">{saveError}</span>
            )}
            <span className="text-xs text-slate-400">
              {saveState === "saving" && "Saving…"}
              {saveState === "saved" && "All changes saved"}
              {saveState === "dirty" && "Unsaved changes"}
            </span>
            <button
              onClick={saveVersion}
              disabled={noteIsEmpty || gen === "streaming"}
              className="rounded bg-emerald-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-800 disabled:bg-slate-300"
            >
              Save note
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-2">
        {showBanner && (
          <div className="lg:col-span-2">
            <div className="flex items-center justify-between rounded border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              <span>
                Returning patient — {routeState?.priorEncounters} prior encounter
                {routeState?.priorEncounters === 1 ? "" : "s"} on record.
              </span>
              <button onClick={() => setShowBanner(false)} className="text-xs underline">
                Dismiss
              </button>
            </div>
          </div>
        )}

        {/* Left: transcript */}
        <section className="flex flex-col gap-3">
          <label className="block">
            <span className="text-xs font-medium text-slate-600">Template</span>
            <select
              value={templateId ?? ""}
              onChange={(e) => setTemplateId(e.target.value ? Number(e.target.value) : null)}
              className="mt-1 w-full rounded border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              <option value="">No template</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <label className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-600">Encounter transcript</span>
              {/* Mutual exclusion with voice editing: the browser only
                  meaningfully supports one active SpeechRecognition session,
                  and dictating INTO the transcript while voice-editing the
                  NOTE would be an incoherent thing to do at the same time. */}
              <DictationControls dictation={dictation} disabled={voiceEdit.state !== "idle"} />
            </div>
            <textarea
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              // Always editable, including while actively listening. The
              // textarea's value is ONLY the committed transcript — never
              // the live interim guess (shown separately below) — so typing
              // here never races against speech events splicing text in
              // mid-keystroke, and continues to work exactly like editing
              // while paused: the physician can fix a word anywhere, and
              // the next finalized chunk appends after whatever the buffer
              // currently holds, dictated or typed.
              placeholder="Paste or type the encounter transcript, or start dictation…"
              className="mt-1 min-h-[24rem] flex-1 resize-y rounded border border-slate-300 bg-white p-3 font-mono text-sm leading-relaxed focus:border-blue-600 focus:outline-none"
            />
            {dictation.state === "listening" && dictation.interim && (
              <p className="mt-1 text-xs italic text-slate-400">
                Hearing: {dictation.interim}
              </p>
            )}
            {dictation.error && (
              <p role="alert" className="mt-1 text-xs text-red-700">{dictation.error}</p>
            )}
            {!dictation.supported && (
              <p className="mt-1 text-xs text-slate-400">
                Voice dictation isn't supported in this browser — try Chrome or Edge.
                Typed and pasted transcripts work as usual.
              </p>
            )}
          </label>
          <button
            onClick={() => generate()}
            disabled={gen === "streaming"}
            className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
          >
            {gen === "streaming" && !autoGenerating ? "Generating…" : "Generate note"}
          </button>

          {gen === "empty" && (
            <p className="rounded border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
              No clinical content detected in this transcript. Nothing was
              generated or saved.
            </p>
          )}
          {gen === "error" && (
            <p role="alert" className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {genError}
            </p>
          )}
        </section>

        {/* Right: SOAP panes */}
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-600">SOAP note</span>
            <VoiceEditControls
              voiceEdit={voiceEdit}
              disabled={dictation.state !== "idle" || gen === "streaming"}
            />
          </div>
          {voiceEdit.state !== "idle" && voiceEdit.interim && (
            <p className="text-xs italic text-slate-400">Hearing: {voiceEdit.interim}</p>
          )}
          {voiceEdit.processing && (
            <p className="text-xs text-slate-400">Applying edit…</p>
          )}
          {!voiceEdit.processing && voiceEdit.lastHeard && voiceEdit.lastMessage && (
            <p className="text-xs text-slate-500">
              Heard "{voiceEdit.lastHeard}" — {voiceEdit.lastMessage}
            </p>
          )}
          {voiceEdit.error && (
            <p role="alert" className="text-xs text-red-700">{voiceEdit.error}</p>
          )}
          {!voiceEdit.supported && (
            <p className="text-xs text-slate-400">
              Voice editing isn't supported in this browser — try Chrome or Edge.
            </p>
          )}
          {historyReferenced !== null && (
            <div className="flex items-center gap-2 rounded border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs text-indigo-800">
              <span aria-hidden>⟲</span>
              History referenced: {historyReferenced} prior encounter
              {historyReferenced === 1 ? "" : "s"}
            </div>
          )}
          {gen === "streaming" && autoGenerating && (
            <div className="flex items-center gap-2 rounded border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-800">
              <span aria-hidden>●</span>
              Auto-updating from dictation…
            </div>
          )}
          {noteDirty && dictation.state !== "idle" && (
            <div className="rounded border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
              Manual edits present — auto-updates paused until you click
              "Generate note" again.
            </div>
          )}
          {SECTIONS.map((section) => (
            <SoapPane
              key={section}
              name={section}
              value={note[section]}
              streaming={gen === "streaming"}
              onChange={(v) => {
                setNote((prev) => ({ ...prev, [section]: v }));
                setNoteDirty(true);
              }}
            />
          ))}
          {icdCodes.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
                ICD-10 codes
              </span>
              <div className="mt-2 flex flex-wrap gap-2">
                {icdCodes.map((c) => (
                  <span
                    key={c.code}
                    title={c.description}
                    className="rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700"
                  >
                    {c.code}
                    <span className="ml-1 font-sans text-slate-500">{c.description}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <IcdSearchWidget
            query={icdQuery}
            onQueryChange={setIcdQuery}
            results={icdResults}
            searching={icdSearching}
            onAppend={appendIcdToAssessment}
          />
        </section>

        {/* Full-width: version history */}
        <section className="lg:col-span-2">
          <VersionHistoryPanel versions={versions} onView={viewVersion} />
        </section>
      </main>

      {viewerOpen && (
        <VersionViewerModal
          version={viewingVersion}
          error={viewingError}
          onClose={() => setViewerOpen(false)}
        />
      )}
    </div>
  );
}

function VersionHistoryPanel({
  versions,
  onView,
}: {
  versions: NoteVersionSummary[];
  onView: (versionNumber: number) => void;
}) {
  if (versions.length === 0) return null;
  // Newest first for the panel — reading top-to-bottom matches how a
  // clinician re-checks "what did I just save", even though the backend
  // returns oldest-first (its natural btree order).
  const rows = [...versions].reverse();
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-3 py-1.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-600">
          Version history
        </span>
      </div>
      <table className="w-full text-sm">
        <tbody>
          {rows.map((v) => (
            <tr
              key={v.version_number}
              onClick={() => onView(v.version_number)}
              className="cursor-pointer border-b border-slate-50 last:border-0 hover:bg-slate-50"
            >
              <td className="px-3 py-2 font-mono text-xs text-slate-500">
                v{v.version_number}
              </td>
              <td className="px-3 py-2 text-slate-700">{v.saved_by_name}</td>
              <td className="px-3 py-2 text-right text-xs text-slate-500">
                {new Date(v.saved_at).toLocaleString(undefined, {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VersionViewerModal({
  version,
  error,
  onClose,
}: {
  version: NoteVersion | null;
  error: boolean;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/40 p-6"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900">
            {version ? `Version ${version.version_number}` : "Version"}
          </h2>
          <button onClick={onClose} className="text-sm text-slate-400 hover:text-slate-600">
            ✕
          </button>
        </div>
        <div className="space-y-4 p-4">
          {error && (
            <p role="alert" className="text-sm text-red-700">
              Could not load this version — try again.
            </p>
          )}
          {!error && !version && (
            <p className="text-sm text-slate-400">Loading…</p>
          )}
          {version &&
            SECTIONS.map((section) => (
              <div key={section}>
                <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {section}
                </span>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                  {version[section] || "—"}
                </p>
              </div>
            ))}
          {version && version.icd_codes.length > 0 && (
            <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3">
              {version.icd_codes.map((c) => (
                <span
                  key={c.code}
                  title={c.description}
                  className="rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700"
                >
                  {c.code}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DictationControls({
  dictation,
  disabled,
}: {
  dictation: ReturnType<typeof useDictation>;
  disabled?: boolean;
}) {
  if (!dictation.supported) return null;
  return (
    <div className="flex items-center gap-2">
      {dictation.state === "idle" && (
        <button
          type="button"
          onClick={dictation.start}
          disabled={disabled}
          className="text-xs font-medium text-blue-700 hover:underline disabled:cursor-not-allowed disabled:text-slate-300 disabled:no-underline"
        >
          ● Start dictation
        </button>
      )}
      {dictation.state === "listening" && (
        <>
          <span className="flex items-center gap-1 text-xs font-medium text-red-600">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" aria-hidden />
            Listening…
          </span>
          <button
            type="button"
            onClick={dictation.pause}
            className="text-xs font-medium text-slate-600 hover:underline"
          >
            Pause
          </button>
          <button
            type="button"
            onClick={dictation.stop}
            className="text-xs font-medium text-slate-600 hover:underline"
          >
            Stop
          </button>
        </>
      )}
      {dictation.state === "paused" && (
        <>
          <span className="text-xs font-medium text-amber-700">Paused</span>
          <button
            type="button"
            onClick={dictation.resume}
            className="text-xs font-medium text-blue-700 hover:underline"
          >
            Resume
          </button>
          <button
            type="button"
            onClick={dictation.stop}
            className="text-xs font-medium text-slate-600 hover:underline"
          >
            Stop
          </button>
        </>
      )}
    </div>
  );
}

function VoiceEditControls({
  voiceEdit,
  disabled,
}: {
  voiceEdit: ReturnType<typeof useVoiceEdit>;
  disabled?: boolean;
}) {
  if (!voiceEdit.supported) return null;
  return (
    <div className="flex items-center gap-2">
      {voiceEdit.state === "idle" && (
        <button
          type="button"
          onClick={voiceEdit.start}
          disabled={disabled}
          className="text-xs font-medium text-blue-700 hover:underline disabled:cursor-not-allowed disabled:text-slate-300 disabled:no-underline"
        >
          ● Start voice edit
        </button>
      )}
      {voiceEdit.state === "listening" && (
        <>
          <span className="flex items-center gap-1 text-xs font-medium text-red-600">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" aria-hidden />
            Listening…
          </span>
          <button
            type="button"
            onClick={voiceEdit.pause}
            className="text-xs font-medium text-slate-600 hover:underline"
          >
            Pause
          </button>
          <button
            type="button"
            onClick={voiceEdit.stop}
            className="text-xs font-medium text-slate-600 hover:underline"
          >
            Stop
          </button>
        </>
      )}
      {voiceEdit.state === "paused" && (
        <>
          <span className="text-xs font-medium text-amber-700">Paused</span>
          <button
            type="button"
            onClick={voiceEdit.resume}
            className="text-xs font-medium text-blue-700 hover:underline"
          >
            Resume
          </button>
          <button
            type="button"
            onClick={voiceEdit.stop}
            className="text-xs font-medium text-slate-600 hover:underline"
          >
            Stop
          </button>
        </>
      )}
    </div>
  );
}

function IcdSearchWidget({
  query,
  onQueryChange,
  results,
  searching,
  onAppend,
}: {
  query: string;
  onQueryChange: (v: string) => void;
  results: IcdCode[];
  searching: boolean;
  onAppend: (c: IcdCode) => void;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
        Search ICD-10 codes
      </span>
      <input
        type="text"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="e.g. knee pain"
        className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm focus:border-blue-600 focus:outline-none"
      />
      {searching && (
        <p className="mt-2 text-xs text-slate-400">Searching…</p>
      )}
      {!searching && results.length > 0 && (
        <ul className="mt-2 divide-y divide-slate-100 rounded border border-slate-100">
          {results.map((r) => (
            <li key={r.code}>
              <button
                type="button"
                onClick={() => onAppend(r)}
                className="flex w-full items-start gap-2 px-2 py-1.5 text-left text-xs hover:bg-slate-50"
              >
                <span className="font-mono font-medium text-slate-700">{r.code}</span>
                <span className="text-slate-500">{r.description}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {!searching && query.trim().length >= 2 && results.length === 0 && (
        <p className="mt-2 text-xs text-slate-400">No matching codes.</p>
      )}
    </div>
  );
}

function SoapPane({
  name,
  value,
  streaming,
  onChange,
}: {
  name: SectionName;
  value: string;
  streaming: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-600">
          {name}
        </span>
        {streaming && value === "" && (
          <span className="text-xs text-slate-400">waiting…</span>
        )}
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={streaming}
        rows={name === "subjective" || name === "plan" ? 5 : 4}
        className="w-full resize-y bg-transparent p-3 text-sm leading-relaxed focus:outline-none"
      />
    </div>
  );
}
