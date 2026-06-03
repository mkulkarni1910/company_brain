# SubStrateOS — Web Chat (Light) End-to-End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the approved light mockup (`mockups/web-chat-light.html`) into a working Next.js web chat wired to the live `brain-api` — ask a question, get a grounded answer with citations, see the real per-answer ranking signals in the right rail, give feedback, multi-turn — running today via the debug-auth path (no Entra SSO).

**Architecture:** Small backend addition (surface the top result's ranker `signal_breakdown` + metadata in `Answer.debug` on request) + a frontend rebuild of `web/` into the three-pane light UI from the mockup, talking to `/query` and `/feedback` with the `x-debug-bypass-auth` header (SSO bypassed, default tenant `t-eval` where the corpus lives). Visual fidelity comes from porting the mockup's CSS verbatim into a global stylesheet and reusing its class names in React components.

**Tech Stack:** Existing — FastAPI/Python (backend), Next.js 14 + React 18 + TypeScript (web). No new deps. MSAL stays installed but is bypassed in debug-auth mode.

**Scope:** light theme only; right context rail always-on (collapses <1180px); landing/empty state → ask → grounded answer + citations + right-rail signals → feedback → multi-turn thread (client-side session, no persistence). No real token streaming (the `/query` endpoint returns a complete answer; show a "thinking" trace during the request). Dark theme, history persistence, real OBO SSO = out of scope.

**Prerequisites:** Phase 4 shipped (`phase-4-zone4-complete`). brain-api runs on :8000 with `ENABLE_DEBUG_AUTH=true`, `ADMIN_API_KEY=dev-admin-key-local`; corpus under tenant `t-eval`. Mockup at `mockups/web-chat-light.html` is the visual source of truth.

---

## Conventions

- Backend tests from `brain-api/` (`uv run pytest`). Frontend from `web/` (`pnpm typecheck`, `pnpm build`). Direct commits to `main`, one per task. After each task: `uv run ruff check .` (backend) / `pnpm typecheck` (frontend) clean.

---

## Task 1: Surface ranking signals in the answer (backend)

**Why:** The right rail shows the top result's Content/People/Activity/Recency signals + candidate count + whether Live Fetch contributed. `/query` currently returns only text + citations. Add an opt-in debug payload so the rail shows *real* signals.

**Files:**
- Modify: `brain-api/app/domain/query.py` (add `include_debug` to QueryRequest)
- Modify: `brain-api/app/orchestrator/kernel.py` (`retrieve_ranked` returns `list[RankedResult]`; `answer` builds `debug`)
- Modify: `brain-api/app/api/retrieve.py` (adapt to RankedResult)
- Modify: `brain-api/tests/test_orchestrator_livefetch.py` (adapt assertions)
- Create: `brain-api/tests/test_answer_debug.py`

- [ ] **Step 1: Add `include_debug` to `QueryRequest`**

In `brain-api/app/domain/query.py`, add to `QueryRequest`:

```python
class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    k: int = 5
    include_debug: bool = False
```

- [ ] **Step 2: Write the failing integration test**

`brain-api/tests/test_answer_debug.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_query_includes_debug_signals_when_requested() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?", "include_debug": True},
            headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["debug"] is not None
        sig = body["debug"]["signals"]
        for k in ("content", "people", "activity", "recency"):
            assert k in sig
        assert body["debug"]["candidates_ranked"] >= 1
        assert "live_used" in body["debug"]


@pytest.mark.integration
def test_query_omits_debug_by_default() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?"},
            headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"},
        )
        assert resp.status_code == 200
        assert resp.json()["debug"] is None
```

- [ ] **Step 3: Run test, expect failure**

Run: `uv run pytest tests/test_answer_debug.py -v -m integration`
Expected: FAIL — `debug` is None even when requested.

- [ ] **Step 4: Make `retrieve_ranked` return `RankedResult` and `answer` build debug**

In `brain-api/app/orchestrator/kernel.py`:

Change the return type and final line of `retrieve_ranked` from `return [r.candidate for r in ranked]` to `return ranked` (so it returns `list[RankedResult]`), and update the signature `-> list[RankedResult]`. Keep the docstring.

In `answer`, replace the retrieval + no-candidate handling + message build:

```python
        ranked = await self.retrieve_ranked(request, user=user)
        if not ranked:
            return Answer(
                text="I don't have information about that.",
                citations=[],
                query_id=query_id,
            )

        candidates = [r.candidate for r in ranked]
        messages = build_grounded_messages(query=request.query, candidates=candidates[:5])
        text = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=800)
        citations = parse_citations_from_answer(text, candidates[:5])

        debug = None
        if request.include_debug:
            top = ranked[0]
            debug = {
                "signals": top.signal_breakdown,
                "final_score": top.final_score,
                "candidates_ranked": len(ranked),
                "live_used": any("live" in r.candidate.sources_hit for r in ranked),
            }
        answer = Answer(text=text, citations=citations, query_id=query_id, debug=debug)

        cache_blob = answer.model_dump()
        cache_blob.pop("query_id", None)
        cache_blob.pop("debug", None)
        await self._cache.set_json(key, cache_blob, ttl_seconds=600)
        return answer
```

(Note: `debug` is stripped before caching and re-derived per request, like `query_id`. The cached-answer branch at the top of `answer` returns `Answer.model_validate({**cached, "query_id": query_id})` — that yields `debug=None` on a cache hit, which is acceptable; the web simply won't show signals for a cached repeat. If you want signals on cache hits too, skip the cache when `include_debug` is true: add `if not request.include_debug:` around the cache-lookup block. DO THIS — wrap the cache GET in `if not request.include_debug:` so debug requests always compute fresh signals.)

- [ ] **Step 5: Adapt `/admin/retrieve` to the new return type**

In `brain-api/app/api/retrieve.py`, `retrieve_ranked` now returns `RankedResult`. Update the endpoint body:

```python
    ranked = await orchestrator.retrieve_ranked(body, user=user)
    return {
        "doc_ids": [r.candidate.chunk.doc_id for r in ranked],
        "candidates": [
            {"doc_id": r.candidate.chunk.doc_id, "chunk_id": r.candidate.chunk.chunk_id,
             "scores": r.signal_breakdown, "rank": r.rank}
            for r in ranked
        ],
    }
```

- [ ] **Step 6: Adapt the orchestrator-livefetch unit tests**

In `brain-api/tests/test_orchestrator_livefetch.py`, `retrieve_ranked` now returns `RankedResult`. Update each assertion that reads `c.chunk.doc_id` over the result to `r.candidate.chunk.doc_id`. Concretely, replace `{c.chunk.doc_id for c in cands}` with `{r.candidate.chunk.doc_id for r in cands}` in all three tests.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_answer_debug.py -v -m integration` → 2 passed.
Run: `uv run pytest tests/test_orchestrator_livefetch.py tests/test_admin_retrieve.py tests/test_query_e2e.py tests/test_orchestrator.py -v` (mix unit+integration) → all pass.
Run: `uv run pytest -m "not integration"` → all unit pass. `uv run ruff check .` → clean.

- [ ] **Step 8: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/domain/query.py brain-api/app/orchestrator/kernel.py brain-api/app/api/retrieve.py brain-api/tests/test_orchestrator_livefetch.py brain-api/tests/test_answer_debug.py
git commit -m "feat: /query surfaces top-result ranking signals in Answer.debug (opt-in)"
```

---

## Task 2: Debug-auth API client + de-SSO the app shell (frontend)

**Why:** The app must run without Entra SSO. Replace the MSAL-gated client with a debug-auth client that sends `x-debug-bypass-auth`, and add a `/feedback` call.

**Files:**
- Modify: `web/.env.local.example`
- Create: `web/.env.local`
- Rewrite: `web/lib/api.ts`
- Rewrite: `web/app/providers.tsx`
- Modify: `web/app/layout.tsx` (drop MSAL provider usage if present)

- [ ] **Step 1: Env**

Replace `web/.env.local.example` with:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
# Dev debug-auth (no SSO): tenant,user,group... — corpus lives under t-eval
NEXT_PUBLIC_DEBUG_AUTH=t-eval,u-demo,t-eval:everyone
NEXT_PUBLIC_USER_NAME=Lokesh Bhoyar
NEXT_PUBLIC_USER_ROLE=Central · Sales
```

Create `web/.env.local` with the same content (this is the file Next actually reads; it's gitignored).

- [ ] **Step 2: Rewrite `web/lib/api.ts`**

```typescript
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
```

- [ ] **Step 3: Simplify `web/app/providers.tsx`**

```typescript
"use client";
import { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
```

(Removes the MSAL provider — debug-auth needs no auth context. `web/lib/msal.ts` can remain unused on disk.)

- [ ] **Step 4: Confirm `layout.tsx` still wraps with `Providers`**

Open `web/app/layout.tsx`; it should import `Providers` from `./providers` and wrap `{children}`. No MSAL imports should remain in layout. If it imports anything from `@azure/msal-*`, remove those imports.

- [ ] **Step 5: Typecheck**

```bash
cd web && pnpm install && pnpm typecheck
```
Expected: clean (the old Chat.tsx still imports MSAL — it's replaced in Task 4; if typecheck fails only inside Chat.tsx, that's expected and fixed in Task 4. If you prefer, temporarily comment Chat's import; it's rewritten next.)

- [ ] **Step 6: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add web/.env.local.example web/lib/api.ts web/app/providers.tsx web/app/layout.tsx
git commit -m "feat(web): debug-auth API client (no SSO) + /feedback; drop MSAL provider"
```

---

## Task 3: Port the light mockup into a global stylesheet (frontend)

**Why:** Reuse the exact approved visual design. Move the mockup's CSS into the app's global stylesheet so React components can use the same class names.

**Files:**
- Rewrite: `web/app/globals.css`
- Modify: `web/app/layout.tsx` (fonts + lang)

- [ ] **Step 1: Build `web/app/globals.css` from the mockup**

Open `mockups/web-chat-light.html` and copy the ENTIRE contents of its `<style>` block into `web/app/globals.css`, with these adjustments:
- Keep all `:root` variables, `body`, `.app`, `.rail`, `.rail--right`, `.topbar`, `.surfaces`, `.scroll`, `.thread`, `.user-*`, `.answer`, `.a-*`, `cite-ref`, `.cites`, `.cite`, `.bars`, `.bar-row`, `.track`, `.fill`, `.fb`, `.composer`, `.ctx-head`, `.stat`, `.rel`, `.trace`, `.badge-fresh`, keyframes, and the media queries — verbatim.
- Remove any leftover unused `.why`/`.why-h` rules (the panel moved to the right rail).
- Do NOT include the `<link>` font tags here; fonts load via `layout.tsx` (Step 2).
- Add at the top: `*{box-sizing:border-box}` is already in the block — keep it.

- [ ] **Step 2: Load fonts + set lang in `layout.tsx`**

In `web/app/layout.tsx`, add the Google Fonts `<link>` in the `<head>` (Next supports a `<head>` via the metadata or a manual tag in the root layout — use a `<link>` in the returned `<html><head>`). Replace the layout body to apply no Tailwind container (the global CSS owns layout). Example:

```typescript
import "./globals.css";
import type { ReactNode } from "react";
import { Providers } from "./providers";

export const metadata = { title: "SubStrateOS — Ask" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
```

- [ ] **Step 3: Disable Tailwind base reset conflicts (if needed)**

The mockup CSS is self-contained. If `tailwind.config.ts` / `globals.css` previously had `@tailwind base/components/utilities`, REMOVE those `@tailwind` directives from `globals.css` (the ported mockup CSS replaces them). Tailwind isn't used by the components. Leave `tailwind.config.ts`/`postcss.config.mjs` in place (harmless) or remove the `@tailwind` lines only.

- [ ] **Step 4: Typecheck + build**

```bash
cd web && pnpm typecheck && pnpm build
```
Expected: builds (page.tsx still old — that's replaced in Task 4; if build fails only on page/Chat content, proceed to Task 4 and re-verify there).

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add web/app/globals.css web/app/layout.tsx
git commit -m "feat(web): port light mockup styles to globals + load fonts"
```

---

## Task 4: Build the live chat UI (frontend)

**Why:** Replace the placeholder page/Chat with the working three-pane chat that renders real `/query` results, the right-rail signals, citations, feedback, suggestions, and multi-turn.

**Files:**
- Rewrite: `web/components/Chat.tsx`
- Rewrite: `web/app/page.tsx`
- Delete: (leave `web/lib/msal.ts` on disk, unused)

- [ ] **Step 1: Rewrite `web/app/page.tsx`**

```typescript
import Chat from "@/components/Chat";
export default function Page() { return <Chat />; }
```

- [ ] **Step 2: Rewrite `web/components/Chat.tsx`**

Build the full three-pane client component. It must reproduce the mockup's structure (`.app` → `.rail` + `.main` + `.rail--right`) using the global CSS classes, and wire live data. Use this implementation:

```typescript
"use client";
import { useState, useRef, useEffect } from "react";
import { postQuery, postFeedback, Answer } from "@/lib/api";

type Turn = { id: string; query: string; answer?: Answer; latencyMs?: number; error?: string; loading: boolean };

const USER_NAME = process.env.NEXT_PUBLIC_USER_NAME ?? "Lokesh Bhoyar";
const USER_ROLE = process.env.NEXT_PUBLIC_USER_ROLE ?? "Central · Sales";
const SUGGESTIONS = [
  "Who is on call right now?",
  "What are our planning priorities?",
  "Latest deployment status",
  "Expense limits for travel",
];
const SIGNAL_META: { key: keyof NonNullable<Answer["debug"]>["signals"]; label: string; color: string }[] = [
  { key: "content", label: "Content", color: "var(--amber)" },
  { key: "people", label: "People", color: "var(--violet)" },
  { key: "activity", label: "Activity", color: "var(--rose)" },
  { key: "recency", label: "Recency", color: "var(--green)" },
];

function initials(name: string) {
  return name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

// Render answer text, converting [n] markers into citation chips.
function AnswerText({ text }: { text: string }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p>
      {parts.map((p, i) => {
        const m = p.match(/^\[(\d+)\]$/);
        if (m) return <cite-ref key={i}>{m[1]}</cite-ref> as any;
        return <span key={i}>{p}</span>;
      })}
    </p>
  );
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // newest answer with debug drives the right rail
  const latest = [...turns].reverse().find((t) => t.answer);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  async function ask(q: string) {
    const query = q.trim();
    if (!query) return;
    const id = crypto.randomUUID();
    setTurns((t) => [...t, { id, query, loading: true }]);
    setInput("");
    try {
      const { answer, latencyMs } = await postQuery(query);
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, answer, latencyMs, loading: false } : x)));
    } catch (e: any) {
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, error: String(e?.message ?? e), loading: false } : x)));
    }
  }

  return (
    <div className="app">
      {/* LEFT RAIL */}
      <aside className="rail">
        <div className="brand">
          <div className="glyph" />
          <div><h1>SubStrate<span style={{ color: "var(--amber)" }}>OS</span></h1><div className="sub">Intelligence Layer</div></div>
        </div>
        <div>
          <h2>Workspace</h2>
          <nav className="nav">
            <a className="active" href="#">Ask</a>
            <a href="#">Discover</a>
            <a href="#">History</a>
          </nav>
        </div>
        <div>
          <h2>Connected sources</h2>
          <div className="sources">
            <div className="src"><span className="dot" />SharePoint<span className="meta">live</span></div>
            <div className="src"><span className="dot" />Teams<span className="meta">live</span></div>
            <div className="src"><span className="dot idle" />Slack<span className="meta">soon</span></div>
            <div className="src"><span className="dot idle" />Jira<span className="meta">soon</span></div>
          </div>
        </div>
        <div className="foot">
          <div className="avatar">{initials(USER_NAME)}</div>
          <div className="who">{USER_NAME}<span>{USER_ROLE}</span></div>
        </div>
      </aside>

      {/* MAIN */}
      <main className="main">
        <header className="topbar">
          <div className="title">Ask the brain</div>
          <span className="tenant">tenant · contoso</span>
          <div className="surfaces">
            <span className="chip on"><span className="d" />Web</span>
            <span className="chip">Teams</span><span className="chip">Slack</span>
            <span className="chip">API</span><span className="chip">MCP</span>
          </div>
        </header>

        <div className="scroll" ref={scrollRef}>
          <div className="thread">
            {turns.length === 0 && (
              <div className="empty">
                <div className="glyph big" />
                <h2 className="empty-h">Ask SubStrateOS anything</h2>
                <p className="empty-p">Grounded answers across SharePoint, Teams, and live sources — ranked for you.</p>
              </div>
            )}
            {turns.map((t) => (
              <div key={t.id}>
                <div className="user-row"><div className="user-msg">{t.query}</div></div>
                {t.loading && (
                  <div className="answer">
                    <div className="a-head"><div className="a-glyph" /><div className="a-name">SubStrate<b>OS</b></div></div>
                    <div className="trace">
                      {["plan", "retrieve", "rank", "ground"].map((s, i) => (
                        <span className="step done" key={s} style={{ opacity: 0.5 }}><span className="num">…</span>{s}{i < 3 && <span className="arrow" style={{ marginLeft: 9 }} />}</span>
                      ))}
                    </div>
                    <div className="a-body" style={{ color: "var(--ink-faint)" }}><p>Thinking…</p></div>
                  </div>
                )}
                {t.error && <div className="answer"><div className="a-body" style={{ color: "var(--rose)" }}><p>{t.error}</p></div></div>}
                {t.answer && (
                  <section className="answer">
                    <div className="a-head">
                      <div className="a-glyph" /><div className="a-name">SubStrate<b>OS</b></div>
                      <div className="badge-fresh"><span className="pulse" />{t.answer.debug?.live_used ? "live · merged" : `grounded · ${t.answer.citations.length} source${t.answer.citations.length === 1 ? "" : "s"}`}</div>
                    </div>
                    <div className="trace">
                      {["plan", "retrieve", "acl re-check", "rank", "ground"].map((s, i, arr) => (
                        <span className="step done" key={s}><span className="num">✓</span>{s}{i < arr.length - 1 && <span className="arrow" style={{ marginLeft: 9 }} />}</span>
                      ))}
                    </div>
                    <div className="a-body"><AnswerText text={t.answer.text} /></div>
                    {t.answer.citations.length > 0 && (
                      <div className="cites">
                        <div className="lbl">Sources</div>
                        {t.answer.citations.map((c, i) => (
                          <a className="cite" key={c.chunk_id} href={c.source_url} target="_blank" rel="noopener noreferrer">
                            <span className="n">[{i + 1}]</span>
                            <div>
                              <div className="c-title">{c.title}</div>
                              <div className="c-snip">{c.snippet}</div>
                              <div className="c-src">{c.doc_id}</div>
                            </div>
                          </a>
                        ))}
                      </div>
                    )}
                    <FeedbackBar answer={t.answer} latencyMs={t.latencyMs} />
                  </section>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="composer">
          <form className="box" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
            <input placeholder="Ask anything across SharePoint, Teams, and live sources…" value={input} onChange={(e) => setInput(e.target.value)} />
            <button className="send" type="submit" aria-label="Send">→</button>
          </form>
          <div className="hintbar">
            {SUGGESTIONS.map((s) => <span className="sg" key={s} onClick={() => ask(s)}>{s}</span>)}
          </div>
        </div>
      </main>

      {/* RIGHT CONTEXT RAIL */}
      <aside className="rail--right">
        <div>
          <div className="ctx-head"><span className="t">Why this ranked</span></div>
          <div style={{ fontSize: 11, color: "var(--ink-faint)", margin: "6px 0 14px" }}>
            {latest?.answer ? `personalized for ${USER_NAME} · ${USER_ROLE}` : "ask a question to see ranking"}
          </div>
          <div className="bars">
            {SIGNAL_META.map((m) => {
              const v = latest?.answer?.debug?.signals?.[m.key] ?? 0;
              return (
                <div className="bar-row" key={m.key}>
                  <div className="k"><span className="sw" style={{ background: m.color }} />{m.label}</div>
                  <div className="track"><div className="fill" style={{ background: m.color, width: `${Math.round(v * 100)}%` }} /></div>
                  <div className="v">{v.toFixed(2)}</div>
                </div>
              );
            })}
          </div>
        </div>
        <div>
          <h2>Answer details</h2>
          <div className="stat"><span>Sources cited</span><span className="v">{latest?.answer?.citations.length ?? "—"}</span></div>
          <div className="stat"><span>Candidates ranked</span><span className="v">{latest?.answer?.debug?.candidates_ranked ?? "—"}</span></div>
          <div className="stat"><span>Latency</span><span className="v">{latest?.latencyMs ? `${latest.latencyMs} ms` : "—"}</span></div>
          <div className="stat"><span>Live fetch</span><span className="v">{latest?.answer?.debug?.live_used ? "yes" : "—"}</span></div>
        </div>
      </aside>
    </div>
  );
}

function FeedbackBar({ answer, latencyMs }: { answer: Answer; latencyMs?: number }) {
  const [sent, setSent] = useState<"thumbs_up" | "thumbs_down" | null>(null);
  const topDoc = answer.citations[0]?.doc_id;
  function send(sig: "thumbs_up" | "thumbs_down") {
    if (!topDoc) return;
    setSent(sig);
    postFeedback(topDoc, sig, answer.query_id);
  }
  return (
    <div className="fb">
      <button className="up" disabled={!!sent} onClick={() => send("thumbs_up")}>{sent === "thumbs_up" ? "✓ Helpful" : "Helpful"}</button>
      <button className="down" disabled={!!sent} onClick={() => send("thumbs_down")}>{sent === "thumbs_down" ? "✓ Noted" : "Not quite"}</button>
      <div className="sep" />
      <span className="cached">{latencyMs ? `${latencyMs} ms` : ""} · feedback → activity pillar</span>
    </div>
  );
}
```

- [ ] **Step 3: Add the missing CSS for new elements (empty state, big glyph)**

`<cite-ref>` is a custom element — register its type so TSX accepts it. Add `web/types.d.ts`:

```typescript
import "react";
declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "cite-ref": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}
```

Append to `web/app/globals.css`:

```css
.empty{margin:auto;text-align:center;padding:80px 0;display:flex;flex-direction:column;align-items:center;gap:14px}
.glyph.big{width:56px;height:56px;border-radius:16px}
.empty-h{font-family:"Fraunces",serif;font-weight:500;font-size:26px;margin:6px 0 0}
.empty-p{color:var(--ink-dim);max-width:420px;margin:0}
.composer .send{font-size:18px;line-height:1}
```

- [ ] **Step 4: Typecheck + build**

```bash
cd web && pnpm typecheck && pnpm build
```
Expected: both clean. Fix any TS errors (the `AnswerText` cast `as any` on the custom element is intentional; if the `JSX.IntrinsicElements` declaration resolves it, remove the cast).

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add web/components/Chat.tsx web/app/page.tsx web/types.d.ts web/app/globals.css
git commit -m "feat(web): live three-pane chat — query, citations, signal rail, feedback, multi-turn"
```

---

## Task 5: End-to-end verification

**Files:** none (verification + a README note).

- [ ] **Step 1: Start brain-api (if not running)**

```bash
cd brain-api && uv run uvicorn app.main:app --port 8000 &
```
Confirm `curl -s localhost:8000/healthz` → ok.

- [ ] **Step 2: Run the web app**

```bash
cd web && pnpm dev
```
Open http://localhost:3000.

- [ ] **Step 3: Manual e2e checks (use the `verify` skill or do by hand)**

- Landing shows the empty state.
- Ask "what is our PTO policy?" → grounded answer with a `[1]` chip + a citation card; right rail fills with **Content/People/Activity/Recency** bars (real numbers from `Answer.debug.signals`), candidates ranked, latency.
- Click **Helpful** → no error (POST /feedback 200; check brain-api logs / network tab → `{"status":"recorded"}`).
- Ask a follow-up (e.g. "expense limits for travel") → second turn appends; right rail updates to the latest answer.
- Click a suggested chip → fires a query.

Capture: confirmation that a real answer rendered with non-zero signal bars and feedback recorded.

- [ ] **Step 4: Headless smoke (optional, scriptable)**

```bash
# with brain-api up:
curl -s -X POST localhost:8000/query -H "x-debug-bypass-auth: t-eval,u-demo,t-eval:everyone" \
  -H "Content-Type: application/json" -d '{"query":"what is our PTO policy?","include_debug":true}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('signals:',d['debug']['signals']);print('ranked:',d['debug']['candidates_ranked'])"
```
Expected: prints a signals dict with content/people/activity/recency and a candidate count — confirming the data the right rail renders.

- [ ] **Step 5: README note + commit**

Append to `brain-api/README.md` (or root README) a "Run the web chat" note:

```markdown
## Run the web chat (SubStrateOS, light)

```
# terminal 1 — API
cd brain-api && uv run uvicorn app.main:app --port 8000
# terminal 2 — web
cd web && cp .env.local.example .env.local && pnpm install && pnpm dev
```
Open http://localhost:3000. Runs via debug-auth (no SSO); queries the `t-eval` tenant where the demo corpus lives.
```

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add README.md brain-api/README.md
git commit -m "docs: how to run the SubStrateOS web chat"
git tag -a web-chat-light-v1 -m "SubStrateOS web chat (light) e2e — grounded answers, signal rail, feedback, multi-turn, debug-auth."
```

---

## Self-Review

**Coverage:** answer rendering + citations (T4), real ranking signals in the right rail (T1 backend + T4 frontend), feedback → activity (T4 + existing /feedback), multi-turn (T4 client state), debug-auth no-SSO run (T2), exact light visual (T3 ports the approved mockup), empty/landing state (T4).

**Signature consistency:** `postQuery → {answer, latencyMs}`, `Answer.debug.signals` keys (content/people/activity/recency) match the backend `signal_breakdown` keys from `PersonalizedRanker` (Task 1 verifies). `retrieve_ranked` now returns `RankedResult` — both callers (answer, /admin/retrieve) and the unit tests updated in T1.

**Risks:** (a) cache hit returns `debug=None` — mitigated by skipping the cache when `include_debug` is true (T1 Step 4). (b) custom `<cite-ref>` element needs the JSX declaration (T4 Step 3). (c) Tailwind base reset could fight the ported CSS — T3 Step 3 removes `@tailwind` directives. (d) the planner adds latency per query (gpt-4o) — acceptable; the "Thinking…" trace covers it.

**Out of scope (tracked):** dark theme, history persistence, real OBO SSO, token streaming.

---

## Execution Handoff

Subagent-driven. T1 is backend (TDD, integration). T2–T4 are frontend (typecheck/build gated; visual fidelity from `mockups/web-chat-light.html`). T5 is e2e verification against the live API.
