// Tiny broker between api.ts (which has no React context) and AuthProvider
// (which owns session state and can render a modal). api.ts calls
// requestReauth() the moment it sees a "Session expired" 401; AuthProvider
// registers the handler that actually shows the re-login modal and resolves
// once the user has successfully re-authenticated.
//
// The dedup below matters: if several requests are in flight when the
// session expires (e.g. the 3s autosave tick lands the same moment the
// physician clicks "Save note"), every one of them calls requestReauth() —
// they must all share the SAME in-flight promise so only one modal ever
// shows, and every caller's retry waits for that one successful re-login
// rather than racing several login attempts against each other.

type ReauthFn = () => Promise<void>;

let reauthHandler: ReauthFn | null = null;
let inFlight: Promise<void> | null = null;

export function registerReauthHandler(fn: ReauthFn): void {
  reauthHandler = fn;
}

export function requestReauth(): Promise<void> {
  if (!reauthHandler) {
    return Promise.reject(new Error("No reauth handler registered"));
  }
  if (!inFlight) {
    inFlight = reauthHandler().finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}

// Same one-registration shape for deactivation — a single global flag
// AuthProvider owns and the app blocks on, not a per-call decision.
type DeactivatedFn = () => void;

let deactivatedHandler: DeactivatedFn | null = null;

export function registerDeactivatedHandler(fn: DeactivatedFn): void {
  deactivatedHandler = fn;
}

export function notifyDeactivated(): void {
  deactivatedHandler?.();
}
