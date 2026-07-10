import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EncounterSummary } from "../api";
import { useAuth } from "../auth";

function formatDate(iso: string): string {
  // Date-only strings (DOBs) must not pass through new Date(iso):
  // "1954-03-17" parses as UTC midnight and renders as Mar 16 in any
  // US timezone. Construct date-only values in local time instead.
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = dateOnly
    ? new Date(+dateOnly[1], +dateOnly[2] - 1, +dateOnly[3])
    : new Date(iso);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [encounters, setEncounters] = useState<EncounterSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<EncounterSummary[]>("/api/encounters")
      .then(setEncounters)
      .catch(() => setError("Could not load encounters — try refreshing."));
  }, []);

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">
            AI Clinical Scribe
          </h1>
          <div className="flex items-center gap-4">
            {user?.role === "admin" && (
              <Link to="/admin" className="text-sm font-medium text-blue-700 hover:underline">
                Admin dashboard
              </Link>
            )}
            <span className="text-sm text-slate-600">{user?.full_name}</span>
            <button
              onClick={logout}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-baseline gap-2">
            <h2 className="text-base font-semibold text-slate-900">
              {user?.role === "admin" ? "All encounters" : "My encounters"}
            </h2>
            {encounters && (
              <span className="text-sm text-slate-400">
                {encounters.length} {encounters.length === 1 ? "encounter" : "encounters"}
              </span>
            )}
          </div>
          {user?.role !== "admin" && (
            <Link
              to="/encounters/new"
              className="flex w-fit items-center gap-1.5 rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-800"
            >
              <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5" aria-hidden="true">
                <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
              </svg>
              New encounter
            </Link>
          )}
        </div>

        {error && <p className="mt-4 text-sm text-red-700">{error}</p>}

        {encounters === null && !error && (
          <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
            <div className="h-9 border-b border-slate-200 bg-slate-50" />
            {[...Array(5)].map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-6 border-b border-slate-100 px-4 py-3 last:border-b-0"
              >
                <div className="h-3.5 w-32 animate-pulse rounded bg-slate-200" />
                <div className="h-3.5 w-20 animate-pulse rounded bg-slate-200" />
                <div className="h-3.5 w-14 animate-pulse rounded bg-slate-200" />
                <div className="h-3.5 w-20 animate-pulse rounded bg-slate-200" />
              </div>
            ))}
          </div>
        )}

        {encounters && encounters.length === 0 && (
          <div className="mt-6 flex flex-col items-center gap-2 rounded border border-dashed border-slate-300 bg-white px-4 py-10 text-center">
            <svg viewBox="0 0 24 24" fill="none" className="h-6 w-6 text-slate-300" aria-hidden="true">
              <rect x="4" y="3" width="16" height="18" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
              <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <p className="text-sm text-slate-500">
              No encounters yet. Your first encounter will appear here.
            </p>
          </div>
        )}

        {encounters && encounters.length > 0 && (
          <div className="mt-6 overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
              <thead>
                <tr className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <th className="border-b border-slate-200 px-4 py-2">Patient</th>
                  <th className="border-b border-slate-200 px-4 py-2">DOB</th>
                  <th className="border-b border-slate-200 px-4 py-2">Status</th>
                  <th className="border-b border-slate-200 px-4 py-2">Date</th>
                </tr>
              </thead>
              <tbody>
                {encounters.map((e) => (
                  <tr
                    key={e.id}
                    onClick={() => navigate(`/encounters/${e.id}`)}
                    className="group cursor-pointer transition-colors hover:bg-slate-50"
                  >
                    <td className="border-b border-l-2 border-slate-100 border-l-transparent px-4 py-2 font-medium text-slate-800 transition-colors group-hover:border-l-blue-600">
                      {e.patient.last_name}, {e.patient.first_name}
                    </td>
                    <td className="border-b border-slate-100 px-4 py-2 tabular-nums text-slate-600">
                      {formatDate(e.patient.dob)}
                    </td>
                    <td className="border-b border-slate-100 px-4 py-2">
                      <span
                        className={
                          e.status === "saved"
                            ? "inline-flex items-center gap-1.5 rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700"
                            : "inline-flex items-center gap-1.5 rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700"
                        }
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-current" />
                        {e.status}
                      </span>
                    </td>
                    <td className="border-b border-slate-100 px-4 py-2 tabular-nums text-slate-600">
                      {formatDate(e.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
