const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
// Local dev: a debug principal (tenant,user,groups). Empty in prod (.env.production),
// where we instead forward the Easy Auth id_token as a Bearer token.
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type Citation = {
  doc_id: string; chunk_id: string; source_url: string; title: string; snippet: string;
};
export type Signals = { content: number; people: number; activity: number; recency: number };
export type AnswerDebug = {
  signals: Signals; final_score: number; candidates_ranked: number; live_used: boolean;
};
export type Answer = {
  query_id: string; text: string; citations: Citation[]; debug?: AnswerDebug | null;
};

// Cached Easy Auth id_token (prod). Fetched once from the same-origin /.auth/me
// endpoint that Container Apps Easy Auth exposes for the signed-in session.
let _idTokenPromise: Promise<string | null> | null = null;

async function easyAuthIdToken(): Promise<string | null> {
  if (!_idTokenPromise) {
    _idTokenPromise = fetch("/.auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => (Array.isArray(data) && data[0]?.id_token) || null)
      .catch(() => null);
  }
  return _idTokenPromise;
}

// In prod (DEBUG_AUTH empty) → Authorization: Bearer <id_token>.
// In local dev → x-debug-bypass-auth header.
async function authHeaders(): Promise<Record<string, string>> {
  if (DEBUG_AUTH) return { "x-debug-bypass-auth": DEBUG_AUTH };
  const token = await easyAuthIdToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function postQuery(query: string): Promise<{ answer: Answer; latencyMs: number }> {
  const t0 = performance.now();
  const resp = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ query, include_debug: true }),
  });
  if (!resp.ok) throw new Error(`brain-api ${resp.status}: ${await resp.text()}`);
  const answer = (await resp.json()) as Answer;
  return { answer, latencyMs: Math.round(performance.now() - t0) };
}

export async function postFeedback(
  doc_id: string, signal: "thumbs_up" | "thumbs_down", query_id?: string
): Promise<void> {
  await fetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ doc_id, signal, query_id }),
  }).catch(() => {/* best-effort */});
}

export type HistoryEntry = { query: string; query_id: string; ts: string };
export type TrendingDoc = {
  doc_id: string; title: string; source: string; source_url: string; snippet: string; score: number;
};
export type SourceActivity = { source: string; events: number; score: number };
export type DiscoverResult = { trending: TrendingDoc[]; by_source: SourceActivity[]; window_days: number };

export async function getHistory(): Promise<HistoryEntry[]> {
  try {
    const resp = await fetch(`${API_BASE}/history`, { headers: { ...(await authHeaders()) } });
    if (!resp.ok) return [];
    return (await resp.json()) as HistoryEntry[];
  } catch {
    return [];
  }
}

export async function getDiscover(): Promise<DiscoverResult> {
  const empty = { trending: [], by_source: [], window_days: 14 };
  try {
    const resp = await fetch(`${API_BASE}/discover`, { headers: { ...(await authHeaders()) } });
    if (!resp.ok) return empty;
    return (await resp.json()) as DiscoverResult;
  } catch {
    return empty;
  }
}

export async function logClick(doc_id: string, source: string, query_id?: string): Promise<void> {
  await fetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ doc_id, signal: "click", source, query_id }),
  }).catch(() => {/* best-effort */});
}
