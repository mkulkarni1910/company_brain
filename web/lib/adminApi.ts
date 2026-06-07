const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export class AdminAuthError extends Error {}

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

// Admin endpoints are gated by the Entra "Admin" group server-side; requests
// just carry the signed-in identity (debug principal locally, Easy Auth in prod).
async function headers(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthIdToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`,
    { ...init, headers: { ...(init.headers ?? {}), ...(await headers()) } });
  if (resp.status === 403) {
    // Signed in, but not in the Entra "Admin" group — the layout shows the
    // access-restricted screen when this fires.
    if (typeof window !== "undefined") window.dispatchEvent(new Event("admin-auth-error"));
    throw new AdminAuthError("admin access denied");
  }
  if (!resp.ok) throw new Error(`admin-api ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as T;
}

export type ConvRunSummary = { id: string; title: string; surface: string; turn_count: number; updated_at: string };
export type ConvTurn = { query: string; answer: { text: string; citations: { title: string; source_url: string; doc_id: string }[] }; ts: string };
export type ConvRunDetail = { id: string; title: string; surface: string; updated_at: string; asker: string | null; turns: ConvTurn[] };

export async function getConversationRuns(): Promise<ConvRunSummary[]> {
  try { return await call<ConvRunSummary[]>("/admin/conversation-runs"); }
  catch { return []; }
}
export async function getConversationRun(id: string): Promise<ConvRunDetail | null> {
  try { return await call<ConvRunDetail>(`/admin/conversation-runs/${encodeURIComponent(id)}`); }
  catch { return null; }
}

export type SourceHealth = { name: string; type: string; status: string; items: number };
export type ActivityItem = { ts: string; actor: string; text: string; kind: string };
export type NeedsItem = { text: string; where: string; severity: "error" | "warning" | "ok" };
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
export type PurgeResult = {
  docs_deleted: number;
  acl_cleared: number | null;
  activity_cleared: number | null;
  errors: string[];
};
export type SurfaceConfig = {
  name: string;
  enabled: boolean;
  installed: boolean;
  workspace_name: string | null;
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
// Admin-consent OAuth (SharePoint / Teams): returns the Microsoft consent URL to redirect to.
export const connectProvider = (provider: string) =>
  call<{ auth_url: string }>(`/admin/connections/oauth/connect?provider=${encodeURIComponent(provider)}`, { method: "POST" });
export const getJob = (id: string) => call<SyncJob>(`/admin/connections/${id}/job`);
export const purgeEverything = () =>
  call<PurgeResult>("/admin/purge", { method: "POST" });
export const purgeSource = (id: string) =>
  call<PurgeResult>(`/admin/connections/${id}/purge`, { method: "POST" });
export const getSurfaces = () => call<SurfaceConfig[]>("/admin/surfaces");
export const patchSurface = (
  name: string,
  enabled: boolean,
  extra?: { installed?: boolean; workspace_name?: string },
) =>
  call<SurfaceConfig>(`/admin/surfaces/${name}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled, ...extra }),
  });

export type BotStatus = {
  teams: { configured: boolean; app_id: string | null };
  slack: { configured: boolean };
  github: { configured: boolean };
};

export const getBotStatus = () => call<BotStatus>("/admin/bot/status");

export type GithubConfig = {
  owner: string | null; repo: string | null; base_branch: string;
  app_configured: boolean; repo_configured: boolean;
};
export const getGithubConfig = () => call<GithubConfig>("/admin/github/config");
export const putGithubConfig = (owner: string, repo: string, base_branch: string) =>
  call<GithubConfig>("/admin/github/config", {
    method: "PUT",
    body: JSON.stringify({ owner, repo, base_branch }),
  });

export async function downloadTeamsManifest(): Promise<void> {
  const resp = await fetch(`${API_BASE}/admin/bot/teams/manifest`, {
    headers: await headers(),
  });
  if (!resp.ok) throw new Error(`manifest ${resp.status}`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "substrateos-teams.zip";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
