const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "leadforge_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (res.status === 401 && token) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Types (mirror the FastAPI schemas) ----

export interface User {
  id: number;
  username: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface Job {
  id: number;
  discovery_id: number;
  user_id: number;
  status: JobStatus;
  progress: Record<string, unknown>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Discovery {
  id: number;
  user_id: number;
  name: string;
  brief: string;
  num_leads: number;
  created_at: string;
  latest_job?: Job | null;
}

export interface DiscoveryCreateInput {
  name?: string;
  brief: string;
  num_leads?: number;
}

export interface Lead {
  id: number;
  discovery_id: number;
  user_id: number;
  name: string;
  category: string | null;
  industry: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  lat: number | null;
  lon: number | null;
  website: string | null;
  email: string | null;
  phone: string | null;
  contact_name: string | null;
  contact_role: string | null;
  source: string | null;
  source_id: string | null;
  score: number;
  score_breakdown: Record<string, unknown>;
  fit_score: number | null;
  fit_reason: string | null;
  what_to_sell: string | null;
  verified: boolean;
  verified_method: string | null;
  status: string;
  dedupe_key: string | null;
  created_at: string;
}

// ---- API helpers ----

export const api = {
  register: (username: string, password: string) =>
    apiFetch<User>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  login: async (username: string, password: string) => {
    const r = await apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setToken(r.access_token);
    return r;
  },

  me: () => apiFetch<User>("/auth/me"),

  listDiscoveries: () => apiFetch<Discovery[]>("/discoveries"),

  getDiscovery: (discoveryId: number) =>
    apiFetch<Discovery>(`/discoveries/${discoveryId}`),

  createDiscovery: (input: DiscoveryCreateInput) =>
    apiFetch<Discovery>("/discoveries", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  listJobs: (discoveryId: number) =>
    apiFetch<Job[]>(`/discoveries/${discoveryId}/jobs`),

  runDiscovery: (discoveryId: number) =>
    apiFetch<Job>(`/discoveries/${discoveryId}/run`, { method: "POST" }),

  deleteDiscovery: (discoveryId: number) =>
    apiFetch<void>(`/discoveries/${discoveryId}`, { method: "DELETE" }),

  listLeads: (discoveryId: number, status?: string) =>
    apiFetch<Lead[]>(
      `/discoveries/${discoveryId}/leads${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
};
