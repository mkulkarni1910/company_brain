"use client";
import { useState } from "react";
import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { postQuery, Answer } from "@/lib/api";

export default function Chat() {
  const { instance } = useMsal();
  const isAuthed = useIsAuthenticated();
  const [q, setQ] = useState("");
  const [a, setA] = useState<Answer | null>(null);
  const [loading, setLoading] = useState(false);

  if (!isAuthed) {
    return (
      <button
        className="rounded bg-indigo-600 px-4 py-2 text-white"
        onClick={() => instance.loginRedirect({ scopes: [process.env.NEXT_PUBLIC_AZURE_API_SCOPE!] })}
      >
        Sign in with Entra
      </button>
    );
  }

  return (
    <div className="space-y-4">
      <form
        className="flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setLoading(true);
          try {
            setA(await postQuery(q));
          } finally {
            setLoading(false);
          }
        }}
      >
        <input
          className="flex-1 rounded border px-3 py-2"
          placeholder="Ask the company brain…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="rounded bg-indigo-600 px-4 py-2 text-white" disabled={loading}>
          {loading ? "…" : "Ask"}
        </button>
      </form>
      {a && (
        <div className="rounded border bg-white p-4">
          <p className="whitespace-pre-wrap">{a.text}</p>
          {a.citations.length > 0 && (
            <ol className="mt-4 list-decimal pl-6 text-sm text-slate-600">
              {a.citations.map((c, i) => (
                <li key={c.chunk_id}>
                  <a className="underline" href={c.source_url} target="_blank">{c.title}</a>
                  <span className="ml-2 italic">{c.snippet}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
