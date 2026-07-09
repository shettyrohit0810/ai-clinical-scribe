// Single fetch wrapper — every page talks to the backend through this.
// Cookies ride along automatically (same-origin in dev via the vite proxy
// and in prod via nginx), so there is no token-handling code in the client
// at all: the httpOnly cookie is invisible to JS by design.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body — keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
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
