const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
// Local dev: a debug principal (tenant,user,groups). Empty in prod (.env.production),
// where we instead forward the Easy Auth id_token as a Bearer token.
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type Citation = {
  doc_id: string; chunk_id: string; source_url: string; title: string; snippet: string;
};
export type Signals = { content: number; people: number; activity: number; recency: number };
export type RelatedPerson = { user_id: string; display_name: string };
export type AnswerDebug = {
  signals: Signals; final_score: number; candidates_ranked: number; live_used: boolean;
  related_people?: RelatedPerson[];
};
export type SkillUsed = { id: string; slug: string; name: string };
export type PendingAction =
  | { type: "github_pr"; run_id: string; title: string; summary: string; path: string; repo: string | null; branch: string }
  | { type: "github_connect"; connect_url: string };
export type Answer = {
  query_id: string; text: string; citations: Citation[]; skill_used?: SkillUsed | null; pending_action?: PendingAction | null; debug?: AnswerDebug | null;
};

export type RunActionResult = { ok: boolean; status: string; pr_url: string | null; message: string };

export async function postRunAction(runId: string, action: "create" | "cancel"): Promise<RunActionResult> {
  const resp = await authedFetch(`${API_BASE}/workflows/runs/${encodeURIComponent(runId)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  if (!resp.ok) throw new Error(`substrateos-api ${resp.status}`);
  return (await resp.json()) as RunActionResult;
}

// Signed-in identity: name from the Entra login, title from the user's Slack
// profile (null when they have none — the UI then shows the name alone).
export type Me = { display_name: string; email: string; title: string | null };

// Avatar initials for a display name, e.g. "Lokesh Bhoyar" -> "LB".
export function initials(name: string) {
  return name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

export async function getMe(): Promise<Me | null> {
  try {
    const resp = await authedFetch(`${API_BASE}/me`);
    if (!resp.ok) return null;
    return (await resp.json()) as Me;
  } catch {
    return null;
  }
}

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

export async function postQuery(query: string, conversationId?: string): Promise<{ answer: Answer; latencyMs: number }> {
  const t0 = performance.now();
  const resp = await authedFetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, include_debug: true, ...(conversationId ? { conversation_id: conversationId } : {}) }),
  });
  if (!resp.ok) throw new Error(`substrateos-api ${resp.status}: ${await resp.text()}`);
  const answer = (await resp.json()) as Answer;
  return { answer, latencyMs: Math.round(performance.now() - t0) };
}

// Fast, context-aware "On it…" line from the small model — fills the pending bubble
// immediately while postQuery (strong model) runs. Best-effort: never throws.
export async function postQueryAck(query: string): Promise<string | null> {
  try {
    const resp = await authedFetch(`${API_BASE}/query/ack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as { ack?: string };
    return data.ack ?? null;
  } catch {
    return null;
  }
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

export type ConversationTurn = { query: string; answer: Answer; ts: string };
export type ConversationSummary = { id: string; title: string; updated_at: string; turn_count: number };
export type Conversation = { id: string; title: string; created_at: string | null; updated_at: string; turns: ConversationTurn[] };

export async function getConversations(): Promise<ConversationSummary[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/conversations`);
    if (!resp.ok) return [];
    return (await resp.json()) as ConversationSummary[];
  } catch { return []; }
}

export async function getConversation(id: string): Promise<Conversation | null> {
  try {
    const resp = await authedFetch(`${API_BASE}/conversations/${encodeURIComponent(id)}`);
    if (!resp.ok) return null;
    return (await resp.json()) as Conversation;
  } catch { return null; }
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

export type TokenMeta = {
  token_id: string;
  name: string;
  masked: string;
  created_at: string;
  last_used_at: string | null;
};
export type TokenCreated = { token: string; meta: TokenMeta };

// The substrateos-api base URL — surfaced in copy-paste snippets in the Connect panels.
export function apiBaseUrl(): string {
  return API_BASE;
}

export async function listTokens(): Promise<TokenMeta[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/tokens`);
    if (!resp.ok) return [];
    return (await resp.json()) as TokenMeta[];
  } catch {
    return [];
  }
}

export async function createToken(name: string): Promise<TokenCreated | null> {
  try {
    const resp = await authedFetch(`${API_BASE}/tokens`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as TokenCreated;
  } catch {
    return null;
  }
}

export async function revokeToken(tokenId: string): Promise<boolean> {
  try {
    const resp = await authedFetch(`${API_BASE}/tokens/${encodeURIComponent(tokenId)}`, {
      method: "DELETE",
    });
    if (!resp.ok) return false;
    const body = (await resp.json()) as { revoked: boolean };
    return body.revoked;
  } catch {
    return false;
  }
}

export type ConnectedSource = { type: string; name: string; status: string };

export async function getConnectedSources(): Promise<ConnectedSource[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/sources`);
    if (!resp.ok) return [];
    return (await resp.json()) as ConnectedSource[];
  } catch {
    return [];
  }
}

export type SurfaceStatus = { name: string; enabled: boolean };

export async function getSurfaces(): Promise<SurfaceStatus[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/surfaces`);
    if (!resp.ok) return [];
    return (await resp.json()) as SurfaceStatus[];
  } catch {
    return [];
  }
}
