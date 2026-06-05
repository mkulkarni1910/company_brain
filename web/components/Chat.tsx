"use client";
import { useState, useRef, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { postQuery, postQueryAck, postFeedback, getConversations, getConversation, logClick, postSearch,
  listTokens, createToken, revokeToken, apiBaseUrl, getSurfaces, getConnectedSources,
  Answer, Citation, ConversationSummary, SearchResponse, TokenMeta, ConnectedSource } from "@/lib/api";
import { getSkills, SkillSummary } from "@/lib/skillsApi";
import SkillsPage from "@/app/skills/page";
import RunsPage from "@/app/runs/page";

type Turn = { id: string; query: string; answer?: Answer; latencyMs?: number; error?: string; loading: boolean; ack?: string };

const USER_NAME = process.env.NEXT_PUBLIC_USER_NAME ?? "Lokesh Bhoyar";
const USER_ROLE = process.env.NEXT_PUBLIC_USER_ROLE ?? "Central · Sales";
const SUGGESTIONS: { text: string; live?: boolean }[] = [
  { text: "Who is on call right now?", live: true },
  { text: "What are our planning priorities?" },
  { text: "Latest deployment status", live: true },
  { text: "Expense limits for travel" },
];
const SIGNAL_META: { key: keyof NonNullable<Answer["debug"]>["signals"]; label: string; color: string }[] = [
  { key: "content", label: "Relevance", color: "var(--amber)" },
  { key: "people", label: "Proximity", color: "var(--violet)" },
  { key: "activity", label: "Engagement", color: "var(--rose)" },
  { key: "recency", label: "Recency", color: "var(--green)" },
];

function initials(name: string) {
  return name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

function relTime(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function sourceIcon(s: string): string {
  return ({ sharepoint: "📁", teams: "💬", uploaded: "📄", slack: "🟪", jira: "🟦", graph: "🌐" } as Record<string, string>)[s] ?? "📄";
}

function ConversationsView({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ConversationSummary[] | null>(null);
  useEffect(() => { getConversations().then(setItems); }, []);
  return (
    <main className="main">
      <header className="topbar"><div className="title">History</div></header>
      <div className="scroll">
        <div className="panel-wrap">
          {items === null && <div className="empty-p">Loading…</div>}
          {items?.length === 0 && <div className="empty-p">No conversations yet — ask something to start one.</div>}
          {items?.map((c) => (
            <button className="hist-row" key={c.id} onClick={() => onOpen(c.id)}>
              <span className="hist-q">{c.title}</span>
              <span className="hist-t">{c.turn_count} turn{c.turn_count === 1 ? "" : "s"} · {relTime(c.updated_at)}</span>
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}

const TIME_FILTERS: { label: string; days: number | null }[] = [
  { label: "Anytime", days: null }, { label: "Past week", days: 7 },
  { label: "Past month", days: 30 }, { label: "Past quarter", days: 90 },
];

function SearchView() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeSources, setActiveSources] = useState<string[]>([]);
  const [timeIdx, setTimeIdx] = useState(0);
  const [author, setAuthor] = useState<string | null>(null);
  const [whoOpen, setWhoOpen] = useState(false);
  const [overview, setOverview] = useState<Answer | null>(null);
  const [ovLoading, setOvLoading] = useState(false);
  const reqId = useRef(0);

  async function run(query: string, sources: string[], days: number | null, authorId: string | null) {
    const text = query.trim();
    if (!text) return;
    const id = ++reqId.current;
    setSubmitted(text); setLoading(true); setOverview(null); setOvLoading(false);
    const opts: { sources?: string[]; date_from?: string; author_id?: string } = {};
    if (sources.length) opts.sources = sources;
    if (days != null) opts.date_from = new Date(Date.now() - days * 864e5).toISOString();
    if (authorId) opts.author_id = authorId;
    // Phase 1: fast results — render immediately.
    const res = await postSearch(text, opts);
    if (id !== reqId.current) return;  // a newer search superseded this one
    setData(res); setLoading(false);
    // Phase 2: AI Overview, fetched separately so the LLM never blocks results,
    // and only when there are results to ground it.
    if (res.results.length > 0) {
      setOvLoading(true);
      postQuery(text)
        .then(({ answer }) => { if (id === reqId.current) { setOverview(answer); setOvLoading(false); } })
        .catch(() => { if (id === reqId.current) setOvLoading(false); });
    }
  }

  function toggleSource(s: string) {
    const next = activeSources.includes(s) ? activeSources.filter((x) => x !== s) : [...activeSources, s];
    setActiveSources(next);
    if (submitted) run(submitted, next, TIME_FILTERS[timeIdx].days, author);
  }

  return (
    <main className="main">
      <div className="searchwrap">
        <form className="searchbar" onSubmit={(e) => { e.preventDefault(); run(q, activeSources, TIME_FILTERS[timeIdx].days, author); }}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="var(--ink-faint)" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
          <input placeholder="Search across SharePoint, Teams, and more…" value={q} onChange={(e) => setQ(e.target.value)} />
          {q && <span className="clr" onClick={() => { setQ(""); setSubmitted(""); setData(null); setOverview(null); setOvLoading(false); }}>✕</span>}
        </form>
        <div className="filters">
          <div className="fchip" onClick={() => { const n = (timeIdx + 1) % TIME_FILTERS.length; setTimeIdx(n); if (submitted) run(submitted, activeSources, TIME_FILTERS[n].days, author); }}>
            {TIME_FILTERS[timeIdx].label} <span className="cv">▾</span>
          </div>
          {submitted && (
            <div className="fchip-wrap">
              <div className="fchip" onClick={() => setWhoOpen((o) => !o)}>
                {author ? (data?.authors.find((a) => a.user_id === author)?.display_name ?? "Who from") : "Who from"} <span className="cv">▾</span>
              </div>
              {whoOpen && (
                <div className="fmenu">
                  <div className="fmenu-item" onClick={() => { setAuthor(null); setWhoOpen(false); run(submitted, activeSources, TIME_FILTERS[timeIdx].days, null); }}>Anyone</div>
                  {(data?.authors ?? []).map((a) => (
                    <div className="fmenu-item" key={a.user_id} onClick={() => { setAuthor(a.user_id); setWhoOpen(false); run(submitted, activeSources, TIME_FILTERS[timeIdx].days, a.user_id); }}>
                      {a.display_name} <span className="fmenu-ct">{a.count}</span>
                    </div>
                  ))}
                  {(!data || data.authors.length === 0) && <div className="fmenu-item" style={{ opacity: .5 }}>No authors</div>}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="scroll">
        {!submitted && <div className="panel-wrap"><div className="empty-p">Search your company knowledge — grounded answers with sources.</div></div>}
        {submitted && (
          <div className="sgrid">
            <div>
              {loading && <div className="empty-p">Searching…</div>}
              {!loading && data && data.results.length > 0 && (ovLoading || overview) && (
                <section className="ai">
                  <div className="ai-head">
                    <svg className="ai-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /><circle cx="12" cy="12" r="3.2" /></svg>
                    <span className="ai-title">AI Overview</span>
                    {overview && <span className="ai-badge">● grounded · {overview.citations.length} sources</span>}
                  </div>
                  {overview
                    ? <div className="ai-body"><AnswerText text={overview.text} citations={overview.citations} /></div>
                    : <div className="ai-body" style={{ color: "var(--ink-faint)" }}>Generating overview…</div>}
                </section>
              )}
              {!loading && (
                <>
                  <p className="rescount">{data?.total ?? 0} results · ranked for you</p>
                  {data?.results.map((h) => (
                    <div className="result" key={h.doc_id}>
                      <div className="ricon">{sourceIcon(h.source)}</div>
                      <div className="rmain">
                        <a className="rtitle" href={h.source_url} target="_blank" rel="noopener noreferrer" onClick={() => logClick(h.doc_id, h.source)}>{h.title}</a>
                        <div className="rmeta">{relTime(h.modified_at)} · <span className="fold">📁 {h.source}</span></div>
                        <div className="rsnip">{h.snippet}</div>
                      </div>
                    </div>
                  ))}
                  {data && data.results.length === 0 && <div className="empty-p">No results.</div>}
                  {data && data.people.length > 0 && (
                    <div className="people">
                      <div className="people-h">People who work on this</div>
                      <div className="pcards">
                        {data.people.map((p) => (
                          <div className="pcard" key={p.user_id}><div className="pav">{initials(p.display_name)}</div><div><div className="nm">{p.display_name}</div>{p.role && <div className="rl">{p.role}</div>}</div></div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <aside className="facets">
              <div className="facet-h" style={{ marginTop: 0 }}><span>Sources</span></div>
              {(data?.facets ?? []).map((f) => (
                <div className={"fac" + (activeSources.includes(f.source) ? " on" : "")} key={f.source} onClick={() => toggleSource(f.source)}>
                  <span className="ic">{sourceIcon(f.source)}</span>{f.source}<span className="ct">{f.count}</span>
                </div>
              ))}
            </aside>
          </div>
        )}
      </div>
    </main>
  );
}

// Render the answer as markdown, turning [n] citation markers into clickable
// chips that link to the matching source. We rewrite `[n]` → `[n](<url> "cite")`
// so react-markdown parses them as links, then style those links as chips via a
// custom `a` renderer (distinguished by the "cite" title). Real markdown links
// in the text render normally.
function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const md = text.replace(/\[(\d+)\]/g, (whole, n: string) => {
    const url = citations[Number(n) - 1]?.source_url;
    return url ? `[${n}](<${url}> "cite")` : whole;
  });
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a({ href, title, children }) {
          if (title === "cite") {
            return (
              <a className="cite-link" href={href} target="_blank" rel="noopener noreferrer">
                <cite-ref>{children}</cite-ref>
              </a>
            );
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          );
        },
      }}
    >
      {md}
    </ReactMarkdown>
  );
}

type Surface = "Web" | "API" | "MCP";

function relAge(iso: string | null | undefined): string {
  return iso ? relTime(iso) : "never";
}

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="code">
      <span className="cp" onClick={() => { navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>
        {copied ? "copied" : "copy"}
      </span>
      <pre className="code-pre">{text}</pre>
    </div>
  );
}

// Token manager matching web-chat-light.html: rich rows (name · created/last-used ·
// masked value), a "＋ Create token" → name form → one-time reveal flow. Wired to
// the real /tokens API (created_at + last_used_at come straight from TokenMeta).
function TokenManager() {
  const [tokens, setTokens] = useState<TokenMeta[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => { listTokens().then(setTokens); }, []);

  async function create() {
    if (busy) return;
    const nm = name.trim() || "new-token";
    setBusy(true);
    const created = await createToken(nm);
    setBusy(false);
    if (created) {
      setFresh({ name: nm, token: created.token });
      setTokens((t) => [created.meta, ...(t ?? [])]);
      setCreating(false); setName("");
    }
  }
  async function revoke(id: string) {
    if (await revokeToken(id)) setTokens((t) => (t ?? []).filter((x) => x.token_id !== id));
  }

  return (
    <>
      <div className="lbl">Personal access tokens</div>
      {tokens === null && <div className="m-sub">Loading…</div>}
      {tokens?.length === 0 && !fresh && <div className="m-sub">No tokens yet — create one below.</div>}
      {tokens?.map((t) => (
        <div className="tok-row" key={t.token_id}>
          <div className="tok-id">
            <div className="tok-name">{t.name}</div>
            <div className="tok-meta">created {relAge(t.created_at)} · last used {relAge(t.last_used_at)}</div>
          </div>
          <code className="tok-val">{t.masked}</code>
          <button className="revoke" onClick={() => revoke(t.token_id)}>Revoke</button>
        </div>
      ))}

      {fresh && (
        <div className="newtok">
          <div className="newtok-head">New token · <b>{fresh.name}</b></div>
          <div className="newtok-top">
            <code className="newtok-val">{fresh.token}</code>
            <button className="copybtn" onClick={() => { navigator.clipboard?.writeText(fresh.token); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <div className="warn">⚠ Copy this now — you won&apos;t be able to see it again.</div>
        </div>
      )}

      {creating && (
        <div className="tok-create">
          <div className="tok-create-lbl">Token name</div>
          <div className="tok-create-row">
            <input className="tok-input" autoFocus placeholder="e.g. my-laptop, ci-pipeline" value={name}
              onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
            <button className="btn" onClick={create} disabled={busy}>{busy ? "Creating…" : "Create"}</button>
            <button className="btn ghost" onClick={() => setCreating(false)}>Cancel</button>
          </div>
        </div>
      )}

      {!creating && (
        <button className="btn" style={{ marginTop: 6 }} onClick={() => { setCreating(true); setFresh(null); }}>
          ＋ Create token
        </button>
      )}
    </>
  );
}

const SURFACE_META: Record<Surface, { icon: string; title: string; sub: string }> = {
  Web: { icon: "🌐", title: "Use SubstrateOS on the web", sub: "You're using it right now" },
  API: { icon: "🔌", title: "Use SubstrateOS via API", sub: "Grounded company context for your own apps & agents" },
  MCP: { icon: "🧩", title: "Use SubstrateOS via MCP", sub: "Connect your AI assistant to SubstrateOS" },
};

function ConnectModal({ surface, onClose }: { surface: Surface; onClose: () => void }) {
  const base = apiBaseUrl();
  const meta = SURFACE_META[surface];

  const curl = `curl -s ${base}/context \\
  -H "Authorization: Bearer $SUBSTRATE_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query":"what is our PTO policy?","top":6}'`;

  const mcpJson = `{
  "mcpServers": {
    "substrateos": {
      "url": "${base}/mcp",
      "headers": { "Authorization": "Bearer sbx_live_…" }
    }
  }
}`;

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div className="cx-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="m-head">
          <div className="m-icon">{meta.icon}</div>
          <div><div className="m-title">{meta.title}</div><div className="m-sub">{meta.sub}</div></div>
          <button className="m-x" onClick={onClose}>✕</button>
        </div>
        <div className="m-body">
          {surface === "API" && (
            <>
              <p className="lead">Call the Context API to pull ranked, ACL-scoped company context into your own LLM — or get a fully grounded answer with citations.</p>
              <div className="lbl">Base URL</div>
              <CodeBlock text={base} />
              <div className="lbl">Endpoints</div>
              <div className="endpoint"><span className="verb">POST</span><code>/context</code><span className="desc">ranked context chunks for a query</span></div>
              <div className="endpoint"><span className="verb">POST</span><code>/query</code><span className="desc">grounded answer + citations</span></div>
              <div className="endpoint"><span className="verb">POST</span><code>/search</code><span className="desc">enterprise search results + facets</span></div>
              <TokenManager />
              <div className="lbl">Example — pull context with curl</div>
              <CodeBlock text={curl} />
            </>
          )}

          {surface === "MCP" && (
            <>
              <p className="lead">SubstrateOS speaks the Model Context Protocol over a hosted HTTP endpoint. Paste the config below into your MCP client (Claude Desktop, Cursor, …) and it can search &amp; ask SubstrateOS — scoped to your access.</p>
              <div className="lbl">MCP server URL</div>
              <CodeBlock text={`${base}/mcp`} />
              <div className="lbl">Config (mcp.json)</div>
              <CodeBlock text={mcpJson} />
              <div className="lbl">Tools your assistant gets</div>
              <div className="toollist">
                <div className="tool"><span className="tn">ask_substrateos</span><span className="td">— grounded answer with citations for a question</span></div>
                <div className="tool"><span className="tn">search_substrateos</span><span className="td">— ranked context/results across connected sources</span></div>
              </div>
              <TokenManager />
            </>
          )}

        </div>
      </div>
    </div>
  );
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [view, setView] = useState<"ask" | "discover" | "history" | "skills" | "runs">("ask");
  const [conversationId, setConversationId] = useState<string>(() => crypto.randomUUID());
  const [connectSurface, setConnectSurface] = useState<Surface | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [surfaceMap, setSurfaceMap] = useState<Record<string, boolean>>({});
  const [connectedSources, setConnectedSources] = useState<ConnectedSource[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [autocomplete, setAutocomplete] = useState<SkillSummary[]>([]);
  const searchParams = useSearchParams();

  useEffect(() => {
    getSurfaces().then((list) => {
      const map: Record<string, boolean> = {};
      for (const s of list) map[s.name] = s.enabled;
      setSurfaceMap(map);
    });
    getConnectedSources().then(setConnectedSources);
  }, []);

  useEffect(() => { getSkills().then(setSkills); }, []);

  useEffect(() => {
    const prefill = searchParams.get("prefill");
    if (prefill) setInput(prefill);
  }, [searchParams]);

  useEffect(() => {
    if (!input.startsWith("/")) { setAutocomplete([]); return; }
    const q = input.slice(1).toLowerCase();
    setAutocomplete(skills.filter(
      (s) => s.slug.includes(q) || s.name.toLowerCase().includes(q)
    ).slice(0, 5));
  }, [input, skills]);

  // Fail-open: if surfaces haven't loaded yet (or fetch failed), treat as enabled.
  const surfaceEnabled = (name: string) => surfaceMap[name] !== false;

  function newChat() { setConversationId(crypto.randomUUID()); setTurns([]); setInput(""); setView("ask"); }

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
    // Immediate acknowledgement from the fast model — fills the pending bubble while
    // the strong model researches. Best-effort; never overrides the real answer.
    postQueryAck(query).then((ack) => {
      if (ack) setTurns((t) => t.map((x) => (x.id === id && x.loading ? { ...x, ack } : x)));
    });
    try {
      const { answer, latencyMs } = await postQuery(query, conversationId);
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, answer, latencyMs, loading: false } : x)));
    } catch (e: any) {
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, error: String(e?.message ?? e), loading: false } : x)));
    }
  }

  if (surfaceMap["web"] === false) {
    return (
      <div className="app app--norail" style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <div style={{ textAlign: "center", maxWidth: 400 }}>
          <div className="glyph big" style={{ margin: "0 auto 24px" }} />
          <h2 style={{ marginBottom: 12 }}>Web app disabled</h2>
          <p style={{ color: "var(--ink-faint)" }}>Your admin has disabled access to the SubstrateOS web interface. Contact your administrator to re-enable it.</p>
        </div>
      </div>
    );
  }

  return (
    <div className={"app" + (view === "ask" ? "" : " app--norail") }>
      {/* LEFT RAIL */}
      <aside className="rail">
        <div className="brand">
          <div className="glyph" />
          <div><h1>Substrate<span style={{ color: "var(--amber)" }}>OS</span></h1><div className="sub">Intelligence Layer</div></div>
        </div>
        <div>
          <h2>Workspace</h2>
          <nav className="nav">
            <button className={view === "ask" ? "active" : ""} onClick={() => setView("ask")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>Ask
            </button>
            <button className={view === "discover" ? "active" : ""} onClick={() => setView("discover")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" /></svg>Discover
            </button>
            <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>History
            </button>
            <button className={view === "skills" ? "active" : ""} onClick={() => setView("skills")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
              </svg>
              Skills
            </button>
            <button className={view === "runs" ? "active" : ""} onClick={() => setView("runs")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" /><rect x="9" y="3" width="6" height="4" rx="1" /><path d="M9 12h6M9 16h6" />
              </svg>
              Runs
            </button>
          </nav>
          <button className="newchat" onClick={newChat}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 5v14M5 12h14" /></svg>New chat
          </button>
        </div>
        <div>
          {connectedSources.length > 0 && (
            <>
              <h2>Connected sources</h2>
              <div className="sources">
                {connectedSources.map((s) => (
                  <div className="src" key={s.type}>
                    <span className={s.status === "syncing" ? "dot amber" : "dot"} />
                    {s.name}
                    <span className="meta">{s.status}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="foot">
          <div className="avatar">{initials(USER_NAME)}</div>
          <div className="who">{USER_NAME}<span>{USER_ROLE}</span></div>
        </div>
      </aside>

      {/* HISTORY / DISCOVER / ASK views */}
      {view === "skills" && <SkillsPage />}
      {view === "runs" && <RunsPage />}
      {view === "history" && <ConversationsView onOpen={async (id) => {
        const conv = await getConversation(id);
        if (!conv) return;
        setConversationId(conv.id);
        setTurns(conv.turns.map((t, i) => ({ id: `${conv.id}:${i}`, query: t.query, answer: t.answer, loading: false })));
        setView("ask");
      }} />}
      {view === "discover" && <SearchView />}
      {view === "ask" && (
      <main className="main">
        <header className="topbar">
          <div className="title">Ask SubstrateOS</div>
          <span className="tenant">tenant · contoso</span>
          <div className="surfaces">
            <button className="chip on" onClick={() => setConnectSurface(null)}><span className="d" />Web</button>
            {(["API", "MCP"] as Surface[]).filter((s) => surfaceEnabled(s.toLowerCase())).map((s) => (
              <button key={s} className={"chip" + (connectSurface === s ? " sel" : "")} onClick={() => setConnectSurface(s)}>{s}</button>
            ))}
          </div>
        </header>

        <div className="scroll" ref={scrollRef}>
          <div className="thread">
            {turns.length === 0 && (
              <div className="empty">
                <div className="glyph big" />
                <h2 className="empty-h">Ask SubstrateOS anything</h2>
                <p className="empty-p">Grounded answers across SharePoint, Teams, and live sources — ranked for you.</p>
              </div>
            )}
            {turns.map((t) => (
              <div className="turn" key={t.id}>
                <div className="user-row"><div className="user-msg">{t.query}</div></div>
                {t.loading && (
                  <div className="answer">
                    <div className="a-head">
                      <div className="a-glyph" /><div className="a-name">Substrate<b>OS</b></div>
                      <div className="badge-working"><span className="dots"><i /><i /><i /></span>working</div>
                    </div>
                    <div className="trace">
                      {["plan", "retrieve", "rank", "ground"].map((s, i, arr) => (
                        <span className={"step" + (i === 0 ? " done" : i === 1 ? " active" : "")} key={s}><span className="num">{i === 0 ? "✓" : "•"}</span>{s}{i < arr.length - 1 && <span className="arrow" style={{ marginLeft: 9 }} />}</span>
                      ))}
                    </div>
                    {t.ack
                      ? <div className="a-ack">{t.ack}</div>
                      : <div className="a-body" style={{ color: "var(--ink-faint)" }}><p>Thinking…</p></div>}
                  </div>
                )}
                {t.error && <div className="answer"><div className="a-body" style={{ color: "var(--rose)" }}><p>{t.error}</p></div></div>}
                {t.answer && (
                  <section className="answer">
                    <div className="a-head">
                      <div className="a-glyph" /><div className="a-name">Substrate<b>OS</b></div>
                      <div className="badge-fresh"><span className="pulse" />{t.answer.debug?.live_used ? "live · merged" : `grounded · ${t.answer.citations.length} source${t.answer.citations.length === 1 ? "" : "s"}`}</div>
                    </div>
                    <div className="trace">
                      {["plan", "retrieve", "acl re-check", "rank", "ground"].map((s, i, arr) => (
                        <span className="step done" key={s}><span className="num">✓</span>{s}{i < arr.length - 1 && <span className="arrow" style={{ marginLeft: 9 }} />}</span>
                      ))}
                    </div>
                    <div className="a-body"><AnswerText text={t.answer.text} citations={t.answer.citations} /></div>
                    {t.answer.skill_used && (
                      <div className="skill-used-badge">▶ via {t.answer.skill_used.name}</div>
                    )}
                    {t.answer.citations.length > 0 && (
                      <div className="cites">
                        <div className="lbl">Sources</div>
                        {t.answer.citations.map((c, i) => (
                          <a className="cite" key={c.chunk_id} href={c.source_url} target="_blank" rel="noopener noreferrer" onClick={() => logClick(c.doc_id, "uploaded", t.answer!.query_id)}>
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
          {autocomplete.length > 0 && (
            <div className="skill-autocomplete">
              {autocomplete.map((s) => (
                <button
                  key={s.id}
                  className="skill-ac-item"
                  onMouseDown={(e) => { e.preventDefault(); setInput(`/${s.slug} `); setAutocomplete([]); }}
                >
                  <span className="skill-ac-name">/{s.slug}</span>
                  <span className="skill-ac-desc">{s.name}</span>
                </button>
              ))}
            </div>
          )}
          <form className="box" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" strokeWidth="1.8"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /><circle cx="12" cy="12" r="3.2" /></svg>
            <input placeholder="Ask anything across SharePoint, Teams, and live sources…" value={input} onChange={(e) => setInput(e.target.value)} />
            <button className="send" type="submit" aria-label="Send">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5M6 11l6-6 6 6" /></svg>
            </button>
          </form>
          <div className="hintbar">
            {SUGGESTIONS.map((s) => (
              <span className="sg" key={s.text} onClick={() => ask(s.text)}>
                {s.text}{s.live && <b> ⚡ live</b>}
              </span>
            ))}
          </div>
        </div>
      </main>
      )}

      {/* RIGHT CONTEXT RAIL */}
      {view === "ask" && (
      <aside className="rail--right">
        <div>
          <div className="ctx-head">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" strokeWidth="1.8"><path d="M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2z" /><path d="M9 21h6" /></svg>
            <span className="t">Why this ranked</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--ink-faint)", margin: "6px 0 14px" }}>
            {latest?.answer ? `Personalized for ${USER_NAME} · ${USER_ROLE}` : "Ask a question to see ranking"}
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
          <div className="stat"><span>Cache</span><span className="v">{latest?.answer?.debug ? "miss" : "—"}</span></div>
          <div className="stat"><span>Live fetch</span><span className="v">{latest?.answer?.debug?.live_used ? "yes" : "—"}</span></div>
        </div>
        <div>
          <h2>Related people</h2>
          {(latest?.answer?.debug?.related_people ?? []).map((p) => (
            <div className="rel" key={p.user_id}>
              <div className="av">{initials(p.display_name)}</div>
              <div className="nm">{p.display_name}<span>cited author</span></div>
            </div>
          ))}
          {!latest?.answer?.debug?.related_people?.length && (
            <div style={{ fontSize: 11, color: "var(--ink-faint)", margin: "6px 0 0" }}>
              {latest?.answer ? "No people linked to these sources." : "Ask a question to see related people"}
            </div>
          )}
        </div>
      </aside>
      )}

      {connectSurface && (
        <ConnectModal surface={connectSurface} onClose={() => setConnectSurface(null)} />
      )}
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
      <button className="up" disabled={!!sent} onClick={() => send("thumbs_up")}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1zM7 11l4-7a2 2 0 0 1 2 2v3h5a2 2 0 0 1 2 2.3l-1.3 7A2 2 0 0 1 16.7 20H7" /></svg>
        {sent === "thumbs_up" ? "Helpful ✓" : "Helpful"}
      </button>
      <button className="down" disabled={!!sent} onClick={() => send("thumbs_down")}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1zM17 13l-4 7a2 2 0 0 1-2-2v-3H6a2 2 0 0 1-2-2.3l1.3-7A2 2 0 0 1 7.3 4H17" /></svg>
        {sent === "thumbs_down" ? "Noted ✓" : "Not quite"}
      </button>
      <div className="sep" />
      <span className="cached">{latencyMs ? `${latencyMs} ms` : ""} · feedback → engagement pillar</span>
    </div>
  );
}
