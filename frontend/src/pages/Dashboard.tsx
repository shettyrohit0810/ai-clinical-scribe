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
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">
            {user?.role === "admin" ? "All encounters" : "My encounters"}
          </h2>
          {user?.role !== "admin" && (
            <Link
              to="/encounters/new"
              className="rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-800"
            >
              New encounter
            </Link>
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
            No encounters yet. Your first encounter will appear here.
          </p>
        )}

        {encounters && encounters.length > 0 && (
          <table className="mt-4 w-full border-separate border-spacing-0 overflow-hidden rounded-lg border border-slate-200 bg-white text-sm shadow-sm">
            <thead>
              <tr className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
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
                  className="cursor-pointer hover:bg-slate-50"
                >
                  <td className="border-b border-slate-100 px-4 py-2 font-medium text-slate-800">
                    {e.patient.last_name}, {e.patient.first_name}
                  </td>
                  <td className="border-b border-slate-100 px-4 py-2 text-slate-600">
                    {formatDate(e.patient.dob)}
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
      </main>
    </div>
  );
}
