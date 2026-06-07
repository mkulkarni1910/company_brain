const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type RefundDecision = {
  found: boolean; order_id: string | null; customer: string | null;
  customer_email?: string | null;
  amount_usd: number | null; order_age_days: number | null;
  policy_limit_usd: number | null; policy_limit_days: number | null;
  auto_approve: boolean; reasoning: string;
};

// Mirrors substrateos-api app/domain/workflow.py PrDraft (UI reads path/summary/title).
export type PrDraft = {
  path?: string | null;
  base_sha?: string | null;
  new_content?: string | null;
  summary?: string | null;
  title?: string | null;
  body?: string | null;
};

export type RunSummary = {
  id: string;
  kind?: "refund" | "approval" | "github_pr";
  status: "running" | "pending_approval" | "approved" | "rejected" | "completed" | "error" | "pending_confirm" | "cancelled" | "needs_attention" | "routed_to_support";
  requester_name: string;
  approver_name: string | null;
  decision: RefundDecision | null;
  handoff_channel?: string | null;
  handoff_ts?: string | null;
  request_text?: string | null;
  approver_source?: string | null;
  surface?: string | null;
  pr_url?: string | null;
  pr_draft?: PrDraft | null;
  created_at: string;
  updated_at: string;
};

export type RunEvent = { ts: string; step: string; detail: string; actor: string };
export type RunDetail = { run: RunSummary; events: RunEvent[] };

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

export async function getRuns(): Promise<RunSummary[]> {
  try {
    const resp = await fetch(`${API_BASE}/runs`, { headers: await userHeaders() });
    if (!resp.ok) return [];
    return (await resp.json()) as RunSummary[];
  } catch {
    return [];
  }
}

export async function getRun(id: string): Promise<RunDetail | null> {
  try {
    const resp = await fetch(`${API_BASE}/runs/${id}`, { headers: await userHeaders() });
    if (!resp.ok) return null;
    return (await resp.json()) as RunDetail;
  } catch {
    return null;
  }
}
