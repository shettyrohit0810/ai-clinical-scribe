import StreamTest from "./StreamTest";

/**
 * Phase 0 shell: header + login placeholder + SSE infrastructure check.
 * Real auth (JWT in httpOnly cookie) lands in Phase 1; the form below is a
 * static placeholder so the deployed skeleton already looks like the app.
 */
export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">
            AI Clinical Scribe
          </h1>
          <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600">
            Phase 0 — walking skeleton
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <section className="max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900">Sign in</h2>
          <p className="mt-1 text-xs text-slate-500">
            Authentication arrives in Phase 1. This placeholder anchors the
            layout.
          </p>
          <form className="mt-4 space-y-3" onSubmit={(e) => e.preventDefault()}>
            <input
              type="email"
              placeholder="Email"
              disabled
              className="w-full rounded border border-slate-300 bg-slate-50 px-3 py-2 text-sm"
            />
            <input
              type="password"
              placeholder="Password"
              disabled
              className="w-full rounded border border-slate-300 bg-slate-50 px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled
              className="w-full rounded bg-slate-400 px-3 py-2 text-sm font-medium text-white"
            >
              Sign in (Phase 1)
            </button>
          </form>
        </section>

        <StreamTest />
      </main>
    </div>
  );
}
