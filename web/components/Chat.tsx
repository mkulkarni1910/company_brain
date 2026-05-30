"use client";
import { useState, useRef, useEffect } from "react";
import { postQuery, postFeedback, Answer } from "@/lib/api";

type Turn = { id: string; query: string; answer?: Answer; latencyMs?: number; error?: string; loading: boolean };

const USER_NAME = process.env.NEXT_PUBLIC_USER_NAME ?? "Alex Kim";
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
        if (m) return <cite-ref key={i}>{m[1]}</cite-ref>;
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
