const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type SkillSummary = {
  id: string; slug: string; name: string; description: string;
  team: string; run_scope: "org" | "team"; enabled: boolean;
  steps: string[]; data_feeds: string[];
  rating: number; rating_count: number; run_count: number;
};

export type SkillFull = SkillSummary & { system_prompt: string; retrieval_config: object | null };

export type SkillCreate = {
  slug: string; name: string; description: string; team: string;
  run_scope?: "org" | "team"; enabled?: boolean;
  steps?: string[]; data_feeds?: string[]; system_prompt: string;
};

export type SkillUpdate = Partial<Omit<SkillCreate, "slug">>;

function getAdminKey(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("adminKey");
}

async function easyAuthToken(): Promise<string | null> {
  return fetch("/.auth/me", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
    .catch(() => null);
}

async function userHeaders(): Promise<Record<string, string>> {
  if (DEBUG_AUTH) return { "x-debug-bypass-auth": DEBUG_AUTH };
  const t = await easyAuthToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function adminHeaders(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const key = getAdminKey();
  if (key) h["x-admin-key"] = key;
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

export async function getSkills(): Promise<SkillSummary[]> {
  try {
    const resp = await fetch(`${API_BASE}/skills`, { headers: await userHeaders() });
    if (!resp.ok) return [];
    return (await resp.json()) as SkillSummary[];
  } catch {
    return [];
  }
}

export async function rateSkill(id: string, rating: number): Promise<void> {
  await fetch(`${API_BASE}/skills/${id}/rate`, {
    method: "POST",
    headers: { ...(await userHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  }).catch(() => {});
}

async function adminCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), ...(await adminHeaders()) },
  });
  if (resp.status === 403) throw new Error("admin key rejected");
  if (!resp.ok) throw new Error(`skills-api ${resp.status}: ${await resp.text()}`);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export async function adminListSkills(): Promise<SkillFull[]> {
  return adminCall<SkillFull[]>("/admin/skills");
}

export async function adminCreateSkill(body: SkillCreate): Promise<SkillFull> {
  return adminCall<SkillFull>("/admin/skills", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function adminUpdateSkill(id: string, body: SkillUpdate & { enabled?: boolean }): Promise<SkillFull> {
  return adminCall<SkillFull>(`/admin/skills/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function adminDeleteSkill(id: string): Promise<void> {
  return adminCall<void>(`/admin/skills/${id}`, { method: "DELETE" });
}
