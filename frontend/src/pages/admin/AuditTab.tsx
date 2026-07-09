import { useEffect, useState } from "react";
import { api, type AuditLogEntry } from "../../api";

export default function AuditTab() {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AuditLogEntry[]>("/api/admin/audit")
      .then(setEntries)
      .catch(() => setError("Could not load the audit log."));
  }, []);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (entries === null) {
    return (
      <div className="space-y-2">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-8 animate-pulse rounded bg-slate-200" />
        ))}
      </div>
    );
  }

  return (
    <table className="w-full border-separate border-spacing-0 overflow-hidden rounded-lg border border-slate-200 bg-white text-sm shadow-sm">
      <thead>
        <tr className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
          <th className="border-b border-slate-200 px-4 py-2">Time</th>
          <th className="border-b border-slate-200 px-4 py-2">User</th>
          <th className="border-b border-slate-200 px-4 py-2">Action</th>
          <th className="border-b border-slate-200 px-4 py-2">Entity</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e) => (
          <tr key={e.id}>
            <td className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
              {new Date(e.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
            </td>
            <td className="border-b border-slate-100 px-4 py-2 text-slate-700">{e.user_name}</td>
            <td className="border-b border-slate-100 px-4 py-2 font-mono text-xs text-slate-700">{e.action}</td>
            <td className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
              {e.entity_type ? `${e.entity_type} #${e.entity_id}` : "—"}
            </td>
          </tr>
        ))}
        {entries.length === 0 && (
          <tr>
            <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
              No audit entries yet.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
