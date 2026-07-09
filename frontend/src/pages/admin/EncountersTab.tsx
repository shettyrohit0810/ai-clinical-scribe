import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type EncounterSummary, type Provider } from "../../api";

function formatDate(iso: string): string {
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = dateOnly
    ? new Date(+dateOnly[1], +dateOnly[2] - 1, +dateOnly[3])
    : new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * All-encounters table with provider + date-range filters. Filters are
 * query params on the SAME GET /api/encounters providers already use
 * (server-side extension, not a parallel admin endpoint) — see
 * backend/app/routers/encounters.py.
 */
export default function EncountersTab() {
  const navigate = useNavigate();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [encounters, setEncounters] = useState<EncounterSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Provider[]>("/api/admin/providers").then(setProviders).catch(() => {});
  }, []);

  useEffect(() => {
    const params = new URLSearchParams();
    if (providerId) params.set("provider_id", providerId);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const qs = params.toString();
    api<EncounterSummary[]>(`/api/encounters${qs ? `?${qs}` : ""}`)
      .then(setEncounters)
      .catch(() => setError("Could not load encounters."));
  }, [providerId, dateFrom, dateTo]);

  const inputCls =
    "rounded border border-slate-300 bg-white px-2 py-1.5 text-sm focus:border-blue-600 focus:outline-none";

  return (
    <div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Provider</span>
          <select
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
            className={`mt-1 block ${inputCls}`}
          >
            <option value="">All providers</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>{p.full_name}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">From</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className={`mt-1 block ${inputCls}`}
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">To</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className={`mt-1 block ${inputCls}`}
          />
        </label>
        {(providerId || dateFrom || dateTo) && (
          <button
            onClick={() => { setProviderId(""); setDateFrom(""); setDateTo(""); }}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            Clear filters
          </button>
        )}
      </div>

      {error && <p className="mt-4 text-sm text-red-700">{error}</p>}

      {encounters === null && !error && (
        <div className="mt-4 space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-10 animate-pulse rounded bg-slate-200" />
          ))}
        </div>
      )}

      {encounters && encounters.length === 0 && (
        <p className="mt-4 rounded border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-500">
          No encounters match these filters.
        </p>
      )}

      {encounters && encounters.length > 0 && (
        <table className="mt-4 w-full border-separate border-spacing-0 overflow-hidden rounded-lg border border-slate-200 bg-white text-sm shadow-sm">
          <thead>
            <tr className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
              <th className="border-b border-slate-200 px-4 py-2">Patient</th>
              <th className="border-b border-slate-200 px-4 py-2">Provider</th>
              <th className="border-b border-slate-200 px-4 py-2">Status</th>
              <th className="border-b border-slate-200 px-4 py-2">Date</th>
            </tr>
          </thead>
          <tbody>
            {encounters.map((e) => (
              <tr
                key={e.id}
                onClick={() => navigate(`/encounters/${e.id}`)}
                className="cursor-pointer hover:bg-slate-50"
              >
                <td className="border-b border-slate-100 px-4 py-2 font-medium text-slate-800">
                  {e.patient.last_name}, {e.patient.first_name}
                </td>
                <td className="border-b border-slate-100 px-4 py-2 text-slate-600">
                  {e.provider_name}
                </td>
                <td className="border-b border-slate-100 px-4 py-2">
                  <span
                    className={
                      e.status === "saved"
                        ? "rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700"
                        : "rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700"
                    }
                  >
                    {e.status}
                  </span>
                </td>
                <td className="border-b border-slate-100 px-4 py-2 text-slate-600">
                  {formatDate(e.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
