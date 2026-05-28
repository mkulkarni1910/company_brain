import { msalInstance, apiScope } from "./msal";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL!;

export type Citation = {
  doc_id: string;
  chunk_id: string;
  source_url: string;
  title: string;
  snippet: string;
};

export type Answer = {
  query_id: string;
  text: string;
  citations: Citation[];
  debug?: Record<string, unknown>;
};

export async function postQuery(query: string): Promise<Answer> {
  const account = msalInstance.getAllAccounts()[0];
  if (!account) throw new Error("not signed in");
  const tok = await msalInstance.acquireTokenSilent({ scopes: [apiScope], account });

  const resp = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok.accessToken}` },
    body: JSON.stringify({ query }),
  });
  if (!resp.ok) throw new Error(`brain-api ${resp.status}: ${await resp.text()}`);
  return resp.json();
}
