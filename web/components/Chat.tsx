"use client";
import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { postQuery, postFeedback, getHistory, logClick, postSearch,
  Answer, Citation, HistoryEntry, SearchResponse } from "@/lib/api";

type Turn = { id: string; query: string; answer?: Answer; latencyMs?: number; error?: string; loading: boolean };

const USER_NAME = process.env.NEXT_PUBLIC_USER_NAME ?? "Alex Kim";
const USER_ROLE = process.env.NEXT_PUBLIC_USER_ROLE ?? "Central · Sales";
const SUGGESTIONS: { text: string; live?: boolean }[] = [
  { text: "Who is on call right now?", live: true },
  { text: "What are our planning priorities?" },
  { text: "Latest deployment status", live: true },
  { text: "Expense limits for travel" },
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

function HistoryView({ onPick }: { onPick: (q: string) => void }) {
  const [items, setItems] = useState<HistoryEntry[] | null>(null);
  useEffect(() => { getHistory().then(setItems); }, []);
  return (
    <main className="main">
      <header className="topbar"><div className="title">History</div></header>
      <div className="scroll">
        <div className="panel-wrap">
          {items === null && <div className="empty-p">Loading…</div>}
          {items?.length === 0 && <div className="empty-p">No questions yet — ask something in Ask.</div>}
          {items?.map((h) => (
            <button className="hist-row" key={h.query_id} onClick={() => onPick(h.query)}>
              <span className="hist-q">{h.query}</span>
              <span className="hist-t">{relTime(h.ts)}</span>
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
  const reqId = useRef(0);

  async function run(query: string, sources: string[], days: number | null) {
    const text = query.trim();
    if (!text) return;
    const id = ++reqId.current;
    setSubmitted(text); setLoading(true);
    const opts: { sources?: string[]; date_from?: string } = {};
    if (sources.length) opts.sources = sources;
    if (days != null) opts.date_from = new Date(Date.now() - days * 864e5).toISOString();
    const res = await postSearch(text, opts);
    if (id !== reqId.current) return;  // a newer search superseded this one
    setData(res); setLoading(false);
  }

  function toggleSource(s: string) {
    const next = activeSources.includes(s) ? activeSources.filter((x) => x !== s) : [...activeSources, s];
    setActiveSources(next);
    if (submitted) run(submitted, next, TIME_FILTERS[timeIdx].days);
  }

  return (
    <main className="main">
      <div className="searchwrap">
        <form className="searchbar" onSubmit={(e) => { e.preventDefault(); run(q, activeSources, TIME_FILTERS[timeIdx].days); }}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="var(--ink-faint)" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
          <input placeholder="Search across SharePoint, Teams, and more…" value={q} onChange={(e) => setQ(e.target.value)} />
          {q && <span className="clr" onClick={() => { setQ(""); setSubmitted(""); setData(null); }}>✕</span>}
        </form>
        <div className="filters">
          <div className="fchip" onClick={() => { const n = (timeIdx + 1) % TIME_FILTERS.length; setTimeIdx(n); if (submitted) run(submitted, activeSources, TIME_FILTERS[n].days); }}>
            {TIME_FILTERS[timeIdx].label} <span className="cv">▾</span>
          </div>
        </div>
      </div>
      <div className="scroll">
        {!submitted && <div className="panel-wrap"><div className="empty-p">Search your company knowledge — grounded answers with sources.</div></div>}
        {submitted && (
          <div className="sgrid">
            <div>
              {loading && <div className="empty-p">Searching…</div>}
              {!loading && data?.answer && (
                <section className="ai">
                  <div className="ai-head">
                    <svg className="ai-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /><circle cx="12" cy="12" r="3.2" /></svg>
                    <span className="ai-title">AI Overview</span>
                    <span className="ai-badge">● grounded · {data.answer.citations.length} sources</span>
                  </div>
                  <div className="ai-body"><AnswerText text={data.answer.text} citations={data.answer.citations} /></div>
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

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [view, setView] = useState<"ask" | "discover" | "history">("ask");
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
            <button className={view === "ask" ? "active" : ""} onClick={() => setView("ask")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>Ask
            </button>
            <button className={view === "discover" ? "active" : ""} onClick={() => setView("discover")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" /></svg>Discover
            </button>
            <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>
              <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>History
            </button>
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

      {/* HISTORY / DISCOVER / ASK views */}
      {view === "history" && <HistoryView onPick={(q) => { setView("ask"); ask(q); }} />}
      {view === "discover" && <SearchView />}
      {view === "ask" && (
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
              <div className="turn" key={t.id}>
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
                    <div className="a-body"><AnswerText text={t.answer.text} citations={t.answer.citations} /></div>
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
      <span className="cached">{latencyMs ? `${latencyMs} ms` : ""} · feedback → activity pillar</span>
    </div>
  );
}
