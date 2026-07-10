import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import EncountersTab from "./admin/EncountersTab";
import ProvidersTab from "./admin/ProvidersTab";
import TemplatesTab from "./admin/TemplatesTab";
import AuditTab from "./admin/AuditTab";

const TABS = ["Encounters", "Providers", "Templates", "Audit log"] as const;
type Tab = (typeof TABS)[number];

export default function AdminDashboard() {
  const { user, logout } = useAuth();
  const [tab, setTab] = useState<Tab>("Encounters");

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <Link to="/admin" className="text-lg font-semibold tracking-tight text-slate-900">
              AI Clinical Scribe
            </Link>
            <span className="rounded bg-slate-800 px-2 py-0.5 text-xs font-medium text-white">Admin</span>
          </div>
          <div className="flex items-center gap-4">
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

      <main className="mx-auto max-w-6xl px-6 py-8">
        <nav className="flex gap-1 border-b border-slate-200">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={
                t === tab
                  ? "border-b-2 border-blue-700 px-3 py-2 text-sm font-medium text-blue-700"
                  : "border-b-2 border-transparent px-3 py-2 text-sm font-medium text-slate-500 hover:text-slate-700"
              }
            >
              {t}
            </button>
          ))}
        </nav>

        <div className="mt-6">
          {tab === "Encounters" && <EncountersTab />}
          {tab === "Providers" && <ProvidersTab />}
          {tab === "Templates" && <TemplatesTab />}
          {tab === "Audit log" && <AuditTab />}
        </div>
      </main>
    </div>
  );
}
