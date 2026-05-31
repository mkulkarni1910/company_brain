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

export type HistoryEntry = { query: string; query_id: string; ts: string };
export type TrendingDoc = {
  doc_id: string; title: string; source: string; source_url: string; snippet: string; score: number;
};
export type SourceActivity = { source: string; events: number; score: number };
export type DiscoverResult = { trending: TrendingDoc[]; by_source: SourceActivity[]; window_days: number };

// Cached Easy Auth id_token (prod). Fetched from the same-origin /.auth/me endpoint
// that Container Apps Easy Auth exposes for the signed-in session.
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

// Force Easy Auth to refresh the stored tokens (using the session's refresh token),
// then drop the cached id_token so the next call re-reads a fresh one from /.auth/me.
async function refreshEasyAuth(): Promise<void> {
  _idTokenPromise = null;
  try {
    await fetch("/.auth/refresh", { credentials: "include" });
  } catch {
    /* best-effort; the retry will re-read /.auth/me regardless */
  }
}

// In prod (DEBUG_AUTH empty) → Authorization: Bearer <id_token>.
// In local dev → x-debug-bypass-auth header.
async function authHeaders(): Promise<Record<string, string>> {
  if (DEBUG_AUTH) return { "x-debug-bypass-auth": DEBUG_AUTH };
  const token = await easyAuthIdToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Fetch with auth. In prod, a 401 (e.g. the Easy Auth id_token expired after ~1h)
// triggers one refresh + retry; if it still fails, the session is gone, so we send
// the user through the Easy Auth login to get a fresh token.
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = async () =>
    fetch(url, { ...init, headers: { ...(init.headers ?? {}), ...(await authHeaders()) } });

  let resp = await send();
  if (resp.status === 401 && !DEBUG_AUTH) {
    await refreshEasyAuth();
    resp = await send();
    if (resp.status === 401 && typeof window !== "undefined") {
      const back = window.location.pathname + window.location.search;
      window.location.href = "/.auth/login/aad?post_login_redirect_uri=" + encodeURIComponent(back);
    }
  }
  return resp;
}

export async function postQuery(query: string): Promise<{ answer: Answer; latencyMs: number }> {
  const t0 = performance.now();
  const resp = await authedFetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, include_debug: true }),
  });
  if (!resp.ok) throw new Error(`brain-api ${resp.status}: ${await resp.text()}`);
  const answer = (await resp.json()) as Answer;
  return { answer, latencyMs: Math.round(performance.now() - t0) };
}

export async function postFeedback(
  doc_id: string, signal: "thumbs_up" | "thumbs_down", query_id?: string
): Promise<void> {
  await authedFetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id, signal, query_id }),
  }).catch(() => {/* best-effort */});
}

export async function getHistory(): Promise<HistoryEntry[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/history`);
    if (!resp.ok) return [];
    return (await resp.json()) as HistoryEntry[];
  } catch {
    return [];
  }
}

export async function getDiscover(): Promise<DiscoverResult> {
  const empty = { trending: [], by_source: [], window_days: 14 };
  try {
    const resp = await authedFetch(`${API_BASE}/discover`);
    if (!resp.ok) return empty;
    return (await resp.json()) as DiscoverResult;
  } catch {
    return empty;
  }
}

export async function logClick(doc_id: string, source: string, query_id?: string): Promise<void> {
  await authedFetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id, signal: "click", source, query_id }),
  }).catch(() => {/* best-effort */});
}

export type SearchHit = {
  doc_id: string; title: string; source: string; source_url: string;
  author_id: string | null; modified_at: string; snippet: string;
};
export type SourceFacet = { source: string; count: number };
export type PersonHit = { user_id: string; display_name: string; role: string | null };
export type PersonFacet = { user_id: string; display_name: string; count: number };
export type SearchResponse = {
  query: string; results: SearchHit[];
  facets: SourceFacet[]; people: PersonHit[]; authors: PersonFacet[]; total: number;
};

export type SearchOpts = { sources?: string[]; date_from?: string; author_id?: string };

export async function postSearch(query: string, opts: SearchOpts = {}): Promise<SearchResponse> {
  const empty: SearchResponse = { query, results: [], facets: [], people: [], authors: [], total: 0 };
  try {
    const resp = await authedFetch(`${API_BASE}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, ...opts }),
    });
    if (!resp.ok) return empty;
    return (await resp.json()) as SearchResponse;
  } catch {
    return empty;
  }
}
