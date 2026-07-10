import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EncounterCreated, type Template } from "../api";

/**
 * New encounter: patient identity + optional template. The backend matches
 * (first, last, dob) against existing patients — the returning-patient
 * result rides along to the workspace via router state for the banner.
 */
export default function NewEncounter() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [dob, setDob] = useState("");
  const [templateId, setTemplateId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Template[]>("/api/templates").then(setTemplates).catch(() => {});
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api<EncounterCreated>("/api/encounters", {
        method: "POST",
        body: JSON.stringify({
          first_name: firstName,
          last_name: lastName,
          dob,
          template_id: templateId ? Number(templateId) : null,
        }),
      });
      navigate(`/encounters/${created.encounter_id}`, {
        state: {
          returning: created.returning,
          priorEncounters: created.prior_encounters,
        },
      });
    } catch {
      setError("Could not start the encounter — check the fields and try again.");
      setBusy(false);
    }
  }

  const inputCls =
    "mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none";
  const sectionLabelCls = "text-xs font-semibold uppercase tracking-wide text-slate-600";

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-5xl px-6 py-3">
          <Link to="/" className="text-xs text-slate-400 hover:text-blue-700 hover:underline">
            ← Back to encounters
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-lg px-6 py-10">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">
          New encounter
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Enter the patient's identity to begin documentation.
        </p>

        <form
          onSubmit={onSubmit}
          className="mt-6 space-y-6 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <div className="space-y-3">
            <span className={sectionLabelCls}>Patient</span>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">First name</span>
                <input required value={firstName} onChange={(e) => setFirstName(e.target.value)} className={inputCls} />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Last name</span>
                <input required value={lastName} onChange={(e) => setLastName(e.target.value)} className={inputCls} />
              </label>
            </div>
            <label className="block">
              <span className="text-xs font-medium text-slate-600">Date of birth</span>
              <input type="date" required value={dob} onChange={(e) => setDob(e.target.value)} className={inputCls} />
            </label>
          </div>

          <div className="space-y-2 border-t border-slate-100 pt-6">
            <span className={sectionLabelCls}>Documentation</span>
            <label className="block">
              <span className="text-xs font-medium text-slate-600">Note template (optional)</span>
              <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} className={inputCls}>
                <option value="">No template</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} — {t.description}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-slate-400">
              Templates control the note's style and structure — content always comes from the transcript.
            </p>
          </div>

          {error && (
            <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
          >
            {busy ? "Starting…" : "Start encounter"}
          </button>
        </form>
      </main>
    </div>
  );
}
