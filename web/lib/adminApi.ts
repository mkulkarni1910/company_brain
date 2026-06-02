const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export class AdminAuthError extends Error {}

export function getAdminKey(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("adminKey");
}
export function setAdminKey(k: string) { sessionStorage.setItem("adminKey", k); }
export function clearAdminKey() { sessionStorage.removeItem("adminKey"); }

let _idTokenPromise: Promise<string | null> | null = null;
async function easyAuthIdToken(): Promise<string | null> {
  if (!_idTokenPromise) {
    _idTokenPromise = fetch("/.auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
      .catch(() => null);
  }
  return _idTokenPromise;
}

async function headers(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const key = getAdminKey();
  if (key) h["x-admin-key"] = key;
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthIdToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`,
    { ...init, headers: { ...(init.headers ?? {}), ...(await headers()) } });
  if (resp.status === 403) {
    clearAdminKey();
    if (typeof window !== "undefined") window.dispatchEvent(new Event("admin-auth-error"));
    throw new AdminAuthError("admin key rejected");
  }
  if (!resp.ok) throw new Error(`admin-api ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as T;
}

export type SourceHealth = { name: string; type: string; status: string; items: number };
export type ActivityItem = { ts: string; actor: string; text: string; kind: string };
export type NeedsItem = { text: string; where: string };
export type AdminStats = {
  active_users: number | null; queries_7d: number | null;
  items_indexed: number | null; sources_live: number;
  source_health: SourceHealth[]; recent_activity: ActivityItem[]; needs_attention: NeedsItem[];
};
export type Connection = {
  connection_id: string; type: string; site_id: string; name: string; web_url: string;
  status: string; item_count: number; last_sync: string | null; error: string | null;
};
export type SiteOption = { site_id: string; name: string; web_url: string };
export type SyncJob = {
  status: string; total: number; processed: number; skipped: number;
  errors: number; truncated: boolean; message: string | null;
};

export const getStats = () => call<AdminStats>("/admin/stats");
export const getConnections = () => call<Connection[]>("/admin/connections");
export const getSites = () => call<SiteOption[]>("/admin/sharepoint/sites");
export const connectSite = (s: SiteOption) =>
  call<{ connection_id: string; status: string }>("/admin/connections",
    { method: "POST", body: JSON.stringify({ site_id: s.site_id, name: s.name, web_url: s.web_url }) });
export const resync = (id: string) =>
  call<{ status: string }>(`/admin/connections/${id}/sync`, { method: "POST" });
export const disconnect = (id: string) =>
  call<{ deleted: boolean }>(`/admin/connections/${id}`, { method: "DELETE" });
// SharePoint admin-consent OAuth: returns the Microsoft consent URL to redirect to.
export const connectSharePoint = () =>
  call<{ auth_url: string }>("/admin/connections/sharepoint/connect", { method: "POST" });
export const getJob = (id: string) => call<SyncJob>(`/admin/connections/${id}/job`);
