const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
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

export async function postQuery(query: string): Promise<{ answer: Answer; latencyMs: number }> {
  const t0 = performance.now();
  const resp = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-debug-bypass-auth": DEBUG_AUTH },
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
    headers: { "Content-Type": "application/json", "x-debug-bypass-auth": DEBUG_AUTH },
    body: JSON.stringify({ doc_id, signal, query_id }),
  }).catch(() => {/* best-effort */});
}
