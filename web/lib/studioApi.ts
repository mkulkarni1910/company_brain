import type { SkillCreate } from "./skillsApi";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type Submission = {
  run_id: string; name: string; slug: string; status: string;
  rejection_note: string | null; submitted_by: string; created_at: string;
  source_text: string | null; skill: SkillCreate | null;
};

async function easyAuthToken(): Promise<string | null> {
  return fetch("/.auth/me", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
    .catch(() => null);
}

async function headers(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`,
    { ...init, headers: { ...(init.headers ?? {}), ...(await headers()) } });
  if (resp.status === 204) return undefined as T;
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* keep status */ }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const draftSkill = (text: string) =>
  call<SkillCreate>("/studio/draft", { method: "POST", body: JSON.stringify({ text }) });

export const submitSkill = (skill: SkillCreate, source_text: string) =>
  call<{ run_id: string; status: string }>("/studio/submit",
    { method: "POST", body: JSON.stringify({ skill, source_text }) });

export const resubmitSkill = (runId: string, skill: SkillCreate, source_text: string) =>
  call<Submission>(`/studio/submissions/${encodeURIComponent(runId)}`,
    { method: "PATCH", body: JSON.stringify({ skill, source_text }) });

export const withdrawSubmission = (runId: string) =>
  call<void>(`/studio/submissions/${encodeURIComponent(runId)}`, { method: "DELETE" });

export const getMySubmissions = () => call<Submission[]>("/studio/submissions");
