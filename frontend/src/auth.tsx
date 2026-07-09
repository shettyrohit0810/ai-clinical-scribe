// Session state lives here and only here. On app load we probe /api/auth/me
// to restore the session from the httpOnly cookie (JS can't read the cookie
// itself — asking the server is the only way to know if we're signed in).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Navigate } from "react-router-dom";
import { ApiError, api, type User } from "./api";
import { registerDeactivatedHandler, registerReauthHandler } from "./sessionExpiry";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // ---- Phase 9: session-expiry re-auth-and-retry ------------------------
  // api.ts calls requestReauth() (sessionExpiry.ts) the moment any request
  // 401s with "Session expired"; that promise resolves only once THIS modal
  // has captured a successful re-login. The pending resolver lives in a ref
  // (not state) because it holds functions, not data to render.
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthError, setReauthError] = useState<string | null>(null);
  const pendingReauth = useRef<{ resolve: () => void; reject: (e: unknown) => void } | null>(null);

  useEffect(() => {
    registerReauthHandler(
      () =>
        new Promise<void>((resolve, reject) => {
          setReauthError(null);
          setReauthOpen(true);
          pendingReauth.current = { resolve, reject };
        }),
    );
    registerDeactivatedHandler(() => {
      setDeactivated(true);
      // Deactivation is terminal — hide the reauth modal (it's about to be
      // replaced by the full-screen notice below) and reject any pending
      // retry rather than leave it hanging forever: nothing will ever
      // resolve it once the modal is gone and unreachable.
      setReauthOpen(false);
      pendingReauth.current?.reject(new Error("Account deactivated"));
      pendingReauth.current = null;
    });
  }, []);

  async function submitReauth(email: string, password: string) {
    setReauthError(null);
    try {
      const me = await api<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setUser(me);
      setReauthOpen(false);
      pendingReauth.current?.resolve();
      pendingReauth.current = null;
    } catch (err) {
      // Wrong password, deactivated, etc. — surfaced in the modal itself;
      // the original failed request keeps waiting until the user either
      // succeeds here or gives up via "Log out instead" below.
      setReauthError(err instanceof ApiError ? err.message : "Sign-in failed — try again.");
    }
  }

  function cancelReauth() {
    setReauthOpen(false);
    pendingReauth.current?.reject(new Error("Re-authentication cancelled"));
    pendingReauth.current = null;
    logout(); // the original request's own catch handles its own state
  }

  // ---- Phase 9: account deactivation -------------------------------------
  // Deactivation is terminal for the session — unlike session expiry there
  // is nothing to retry (the account itself is blocked), so this replaces
  // the whole app with a blocking notice rather than trying to recover.
  const [deactivated, setDeactivated] = useState(false);

  useEffect(() => {
    api<User>("/api/auth/me")
      .then(setUser)
      .catch(() => setUser(null)) // not signed in — expected on first visit
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const me = await api<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    // Deliberately a raw fetch, not api(): logout is the ONE call that must
    // never trigger the session-expiry interceptor. If the same expired
    // cookie that got us here also makes THIS request 401, going through
    // api() would call requestReauth() again and reopen the very modal the
    // user just clicked "Log out instead" to escape. Best-effort either way
    // — the server-side cookie clear doesn't need to succeed for the client
    // to consider itself logged out.
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {deactivated ? <DeactivatedScreen /> : children}
      {reauthOpen && (
        <ReauthModal
          email={user?.email ?? ""}
          error={reauthError}
          onSubmit={submitReauth}
          onCancel={cancelReauth}
        />
      )}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    // Skeleton, not a blank flash: the /me probe usually resolves in <100ms.
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        Loading…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// Client-side gate only — a UX nicety, not the security boundary. Every
// admin endpoint enforces role=admin server-side via require_admin
// (app/auth.py); a provider hitting /admin directly just bounces to "/",
// and any API call they made from there would 403 regardless.
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        Loading…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function ReauthModal({
  email,
  error,
  onSubmit,
  onCancel,
}: {
  email: string;
  error: string | null;
  onSubmit: (email: string, password: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [emailValue, setEmailValue] = useState(email);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await onSubmit(emailValue, password);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/50 px-4">
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-xl">
        <h2 className="text-base font-semibold text-slate-900">Session expired</h2>
        <p className="mt-1 text-sm text-slate-500">
          Sign in again to continue — nothing you were working on has been lost.
        </p>
        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <label className="block">
            <span className="text-xs font-medium text-slate-600">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={emailValue}
              onChange={(e) => setEmailValue(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-600">Password</span>
            <input
              type="password"
              required
              autoFocus
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none"
            />
          </label>
          {error && (
            <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-300"
          >
            {busy ? "Signing in…" : "Sign in and retry"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="w-full text-center text-xs text-slate-400 hover:text-slate-600 hover:underline"
          >
            Log out instead
          </button>
        </form>
      </div>
    </div>
  );
}

function DeactivatedScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-red-200 bg-white p-6 text-center shadow-sm">
        <h1 className="text-base font-semibold text-red-700">Account deactivated</h1>
        <p className="mt-2 text-sm text-slate-600">
          Your draft is preserved. Contact your administrator to restore access
          — once your account is reactivated, reload this page to sign back in.
        </p>
      </div>
    </div>
  );
}
