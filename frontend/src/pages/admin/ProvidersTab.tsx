import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type Provider } from "../../api";

/** Add / deactivate providers. Deactivation takes effect on the provider's
 * very next request (see backend/app/auth.py get_current_user) — no
 * refresh or client-side propagation needed for the demo. */
export default function ProvidersTab() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  function refresh() {
    api<Provider[]>("/api/admin/providers").then(setProviders).catch(() => setError("Could not load providers."));
  }

  useEffect(refresh, []);

  async function toggleActive(p: Provider) {
    try {
      await api(`/api/admin/providers/${p.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !p.is_active }),
      });
      refresh();
    } catch {
      setError(`Could not update ${p.full_name}.`);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Providers</h3>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800"
        >
          {showForm ? "Cancel" : "Add provider"}
        </button>
      </div>

      {showForm && (
        <AddProviderForm
          onCreated={() => { setShowForm(false); refresh(); }}
        />
      )}

      {error && <p className="mt-4 text-sm text-red-700">{error}</p>}

      {providers === null && !error && (
        <div className="mt-4 h-24 animate-pulse rounded bg-slate-200" />
      )}

      {providers && (
        <table className="mt-4 w-full border-separate border-spacing-0 overflow-hidden rounded-lg border border-slate-200 bg-white text-sm shadow-sm">
          <thead>
            <tr className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
              <th className="border-b border-slate-200 px-4 py-2">Name</th>
              <th className="border-b border-slate-200 px-4 py-2">Email</th>
              <th className="border-b border-slate-200 px-4 py-2">Status</th>
              <th className="border-b border-slate-200 px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.id} className="hover:bg-slate-50">
                <td className="border-b border-slate-100 px-4 py-2 font-medium text-slate-800">
                  {p.full_name}
                </td>
                <td className="border-b border-slate-100 px-4 py-2 text-slate-600">{p.email}</td>
                <td className="border-b border-slate-100 px-4 py-2">
                  <span
                    className={
                      p.is_active
                        ? "rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700"
                        : "rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500"
                    }
                  >
                    {p.is_active ? "active" : "deactivated"}
                  </span>
                </td>
                <td className="border-b border-slate-100 px-4 py-2 text-right">
                  <button
                    onClick={() => toggleActive(p)}
                    className="text-xs font-medium text-blue-700 hover:underline"
                  >
                    {p.is_active ? "Deactivate" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AddProviderForm({ onCreated }: { onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/admin/providers", {
        method: "POST",
        body: JSON.stringify({ email, full_name: fullName, password }),
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create provider.");
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none";

  return (
    <form onSubmit={onSubmit} className="mt-4 grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-3">
      <label className="block">
        <span className="text-xs font-medium text-slate-600">Full name</span>
        <input required value={fullName} onChange={(e) => setFullName(e.target.value)} className={inputCls} />
      </label>
      <label className="block">
        <span className="text-xs font-medium text-slate-600">Email</span>
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} />
      </label>
      <label className="block">
        <span className="text-xs font-medium text-slate-600">Temporary password</span>
        <input
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={inputCls}
        />
      </label>
      {error && (
        <p role="alert" className="sm:col-span-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="sm:col-span-3 rounded bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
      >
        {busy ? "Creating…" : "Create provider"}
      </button>
    </form>
  );
}
