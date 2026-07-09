// Single fetch wrapper — every page talks to the backend through this.
// Cookies ride along automatically (same-origin in dev via the vite proxy
// and in prod via nginx), so there is no token-handling code in the client
// at all: the httpOnly cookie is invisible to JS by design.

import { notifyDeactivated, requestReauth } from "./sessionExpiry";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function readErrorDetail(res: Response): Promise<string> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // non-JSON error body — keep statusText
  }
  return detail;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (res.ok) return res.json();

  const detail = await readErrorDetail(res);

  // "Session expired" (token past its 30-min expiry, cookie still valid —
  // see auth.py) is recoverable without losing whatever this call was
  // trying to do: pause here for a successful re-login, then replay the
  // EXACT same request once. Every caller of api() gets this for free —
  // flushAutosave, saveVersion, admin actions, all of it — with zero
  // caller-side retry logic, because the recovery lives in the one place
  // every request already passes through.
  if (res.status === 401 && detail === "Session expired") {
    await requestReauth();
    const retry = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
    if (retry.ok) return retry.json();
    throw new ApiError(retry.status, await readErrorDetail(retry));
  }

  // Deactivation (403 "Account deactivated") is NOT recoverable by
  // retrying or re-authenticating — the account itself is blocked, so
  // every subsequent call would 403 again regardless. Flag it globally
  // once; the app renders a full-screen "your draft is preserved" notice
  // instead of trying to keep the workspace usable.
  if (res.status === 403 && detail === "Account deactivated") {
    notifyDeactivated();
  }

  throw new ApiError(res.status, detail);
}

// ---- API types (mirror backend/app/schemas.py) ----

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: "provider" | "admin";
}

export interface Patient {
  id: number;
  first_name: string;
  last_name: string;
  dob: string;
}

export interface EncounterSummary {
  id: number;
  patient: Patient;
  status: "draft" | "saved";
  created_at: string;
  updated_at: string;
  provider_id: number;
  provider_name: string;
}

export interface IcdCode {
  code: string;
  description: string;
}

export interface DraftNote {
  subjective: string;
  objective: string;
  assessment: string;
  plan: string;
  icd_codes: IcdCode[];
}

export interface NoteVersion {
  version_number: number;
  subjective: string;
  objective: string;
  assessment: string;
  plan: string;
  icd_codes: IcdCode[];
  saved_by: number;
  saved_at: string;
}

export interface NoteVersionSummary {
  version_number: number;
  saved_by: number;
  saved_by_name: string;
  saved_at: string;
}

export interface EncounterDetail extends EncounterSummary {
  transcript: string;
  template_id: number | null;
  draft_note: DraftNote | null;
  latest_version: NoteVersion | null;
}

export interface EncounterCreated {
  encounter_id: number;
  patient: Patient;
  returning: boolean;
  prior_encounters: number;
}

export interface Template {
  id: number;
  name: string;
  description: string;
}

// ---- Phase 6: admin dashboard ----

export interface Provider {
  id: number;
  email: string;
  full_name: string;
  role: "provider" | "admin";
  is_active: boolean;
  created_at: string;
}

export interface TemplateAdmin {
  id: number;
  name: string;
  description: string;
  instructions: string;
  is_active: boolean;
  created_by: number;
  created_at: string;
  updated_at: string;
}

export interface AuditLogEntry {
  id: number;
  user_id: number;
  user_name: string;
  action: string;
  entity_type: string | null;
  entity_id: number | null;
  created_at: string;
}
