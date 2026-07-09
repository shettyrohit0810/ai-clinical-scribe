import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type TemplateAdmin } from "../../api";

/** Template CRUD. `instructions` styles generation output — see
 * app/prompts.py TEMPLATE_FRAME — and is read fresh from the DB at
 * generation time (app/routers/generation.py), so an edit here takes
 * effect on the provider's very next "Generate note" with no refresh,
 * cache bust, or push channel involved. */
export default function TemplatesTab() {
  const [templates, setTemplates] = useState<TemplateAdmin[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<TemplateAdmin | null>(null);

  function refresh() {
    api<TemplateAdmin[]>("/api/admin/templates").then(setTemplates).catch(() => setError("Could not load templates."));
  }

  useEffect(refresh, []);

  async function toggleActive(t: TemplateAdmin) {
    try {
      await api(`/api/admin/templates/${t.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !t.is_active }),
      });
      refresh();
    } catch {
      setError(`Could not update ${t.name}.`);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Note templates</h3>
        <button
          onClick={() => setShowCreate((s) => !s)}
          className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800"
        >
          {showCreate ? "Cancel" : "New template"}
        </button>
      </div>

      {showCreate && (
        <TemplateForm
          onSaved={() => { setShowCreate(false); refresh(); }}
          onCancel={() => setShowCreate(false)}
        />
      )}

      {error && <p className="mt-4 text-sm text-red-700">{error}</p>}

      {templates === null && !error && (
        <div className="mt-4 h-24 animate-pulse rounded bg-slate-200" />
      )}

      {templates && (
        <table className="mt-4 w-full border-separate border-spacing-0 overflow-hidden rounded-lg border border-slate-200 bg-white text-sm shadow-sm">
          <thead>
            <tr className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
              <th className="border-b border-slate-200 px-4 py-2">Name</th>
              <th className="border-b border-slate-200 px-4 py-2">Description</th>
              <th className="border-b border-slate-200 px-4 py-2">Status</th>
              <th className="border-b border-slate-200 px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {templates.map((t) => (
              <tr key={t.id} className="hover:bg-slate-50">
                <td className="border-b border-slate-100 px-4 py-2 font-medium text-slate-800">{t.name}</td>
                <td className="border-b border-slate-100 px-4 py-2 text-slate-600">{t.description}</td>
                <td className="border-b border-slate-100 px-4 py-2">
                  <span
                    className={
                      t.is_active
                        ? "rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700"
                        : "rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500"
                    }
                  >
                    {t.is_active ? "active" : "inactive"}
                  </span>
                </td>
                <td className="border-b border-slate-100 px-4 py-2 text-right">
                  <button onClick={() => setEditing(t)} className="mr-3 text-xs font-medium text-blue-700 hover:underline">
                    Edit
                  </button>
                  <button onClick={() => toggleActive(t)} className="text-xs font-medium text-slate-600 hover:underline">
                    {t.is_active ? "Deactivate" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/40 p-6" onClick={() => setEditing(null)}>
          <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-lg bg-white p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <TemplateForm
              existing={editing}
              onSaved={() => { setEditing(null); refresh(); }}
              onCancel={() => setEditing(null)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function TemplateForm({
  existing,
  onSaved,
  onCancel,
}: {
  existing?: TemplateAdmin;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [instructions, setInstructions] = useState(existing?.instructions ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (existing) {
        await api(`/api/admin/templates/${existing.id}`, {
          method: "PATCH",
          body: JSON.stringify({ name, description, instructions }),
        });
      } else {
        await api("/api/admin/templates", {
          method: "POST",
          body: JSON.stringify({ name, description, instructions }),
        });
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save template.");
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none";

  return (
    <form onSubmit={onSubmit} className="mt-4 space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <h4 className="text-sm font-semibold text-slate-900">
        {existing ? `Edit ${existing.name}` : "New template"}
      </h4>
      <label className="block">
        <span className="text-xs font-medium text-slate-600">Name</span>
        <input required value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
      </label>
      <label className="block">
        <span className="text-xs font-medium text-slate-600">Description</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} />
      </label>
      <label className="block">
        <span className="text-xs font-medium text-slate-600">
          Instructions (style/structure guidance for note generation)
        </span>
        <textarea
          required
          rows={6}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          className={`${inputCls} font-mono text-xs leading-relaxed`}
        />
      </label>
      {error && (
        <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
