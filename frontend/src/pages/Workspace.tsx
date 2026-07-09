import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  api,
  type DraftNote,
  type EncounterDetail,
  type IcdCode,
  type Template,
} from "../api";

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
  const [saveState, setSaveState] = useState<SaveState>("clean");
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [showBanner, setShowBanner] = useState(routeState?.returning ?? false);

  const sourceRef = useRef<EventSource | null>(null);
  const loadedRef = useRef(false);

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
    return () => sourceRef.current?.close();
  }, [id]);

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
  async function generate() {
    sourceRef.current?.close();
    await flushAutosave(); // server generates from the freshest transcript
    setNote(EMPTY_NOTE);
    setIcdCodes([]);
    setGen("streaming");

    const source = new EventSource(`/api/encounters/${id}/generate`);
    sourceRef.current = source;
    source.addEventListener("section", (e) => {
      const { section, delta } = JSON.parse((e as MessageEvent).data);
      setNote((prev) => ({ ...prev, [section]: prev[section as SectionName] + delta }));
    });
    source.addEventListener("icd_codes", (e) => {
      setIcdCodes(JSON.parse((e as MessageEvent).data));
    });
    source.addEventListener("no_clinical_content", () => {
      source.close();
      setGen("empty");
    });
    source.addEventListener("error", (e) => {
      source.close();
      const data = (e as MessageEvent).data;
      setGenError(data ? JSON.parse(data).message : "Connection lost — try again.");
      setGen("error");
    });
    source.addEventListener("done", () => {
      source.close();
      setGen("done");
    });
    // EventSource network failure (no server event) also lands here:
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) return;
      source.close();
      setGenError("Connection lost — your transcript is safe. Try again.");
      setGen("error");
    };
  }

  // ---- save as version ---------------------------------------------------
  async function saveVersion() {
    await flushAutosave();
    const result = await api<{ version_number: number }>(
      `/api/encounters/${id}/save`,
      { method: "POST", body: JSON.stringify({ ...note, icd_codes: icdCodes }) },
    );
    setSavedVersion(result.version_number);
    setDetail((d) => (d ? { ...d, status: "saved" } : d));
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
            <span className="text-xs font-medium text-slate-600">Encounter transcript</span>
            <textarea
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder="Paste or type the encounter transcript…"
              className="mt-1 min-h-[24rem] flex-1 resize-y rounded border border-slate-300 bg-white p-3 font-mono text-sm leading-relaxed focus:border-blue-600 focus:outline-none"
            />
          </label>
          <button
            onClick={generate}
            disabled={gen === "streaming"}
            className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
          >
            {gen === "streaming" ? "Generating…" : "Generate note"}
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
          {SECTIONS.map((section) => (
            <SoapPane
              key={section}
              name={section}
              value={note[section]}
              streaming={gen === "streaming"}
              onChange={(v) => setNote((prev) => ({ ...prev, [section]: v }))}
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
        </section>
      </main>
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
