import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const me = await login(email, password);
      navigate(me.role === "admin" ? "/admin" : "/", { replace: true });
    } catch (err) {
      // Backend messages are already user-appropriate ("Invalid email or
      // password", "Account deactivated") — show them verbatim.
      setError(err instanceof ApiError ? err.message : "Sign-in failed — try again");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-center text-2xl font-semibold tracking-tight text-slate-900">
          AI Clinical Scribe
        </h1>
        <p className="mt-1 text-center text-sm text-slate-500">
          Sign in to your clinical workspace
        </p>

        <form
          onSubmit={onSubmit}
          className="mt-6 space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <label htmlFor="login-email" className="block">
            <span className="text-xs font-medium text-slate-600">Email</span>
            <input
              id="login-email"
              type="email"
              required
              autoFocus
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "login-error" : undefined}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
            />
          </label>
          <label htmlFor="login-password" className="block">
            <span className="text-xs font-medium text-slate-600">Password</span>
            <input
              id="login-password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "login-error" : undefined}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
            />
          </label>

          {error && (
            <p
              id="login-error"
              role="alert"
              className="rounded bg-red-50 px-3 py-2 text-sm text-red-700"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:ring-offset-2 disabled:bg-slate-300"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <details className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-slate-500">
          <summary className="cursor-pointer select-none text-xs font-medium text-slate-600">
            Demo accounts
          </summary>
          <dl className="mt-2 space-y-1 text-xs">
            <div className="flex items-center justify-between gap-3">
              <dt className="text-slate-400">Provider</dt>
              <dd className="font-mono text-slate-600">sarah.chen@clinic.example</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-slate-400">Admin</dt>
              <dd className="font-mono text-slate-600">admin@clinic.example</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-slate-400">Password</dt>
              <dd className="font-mono text-slate-600">ScribeDemo1!</dd>
            </div>
          </dl>
        </details>
      </div>
    </div>
  );
}
