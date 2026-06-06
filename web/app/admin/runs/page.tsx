"use client";
import { useEffect, useState } from "react";
import { getRuns, getRun, RunSummary, RunDetail } from "@/lib/runsApi";
import {
  getConversationRuns, getConversationRun, ConvRunSummary, ConvRunDetail,
} from "@/lib/adminApi";

function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("en-US", { hour12: false }); } catch { return iso; }
}
function fmtClock(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }); } catch { return iso; }
}
function usd(v: number | null | undefined): string { return v == null ? "—" : `$${v.toLocaleString("en-US")}`; }
function surfaceLabel(s: string): string {
  return s === "slack" ? "Slack" : s === "teams" ? "Microsoft Teams" : "Web chat";
}
function snippet(t: string, n = 90): string { return t.length > n ? t.slice(0, n).trimEnd() + "…" : t; }

const WF_STATUS: Record<string, { cls: string; label: string }> = {
  pending_approval: { cls: "stopped", label: "Stopped for approval" },
  running: { cls: "running", label: "Running" },
  approved: { cls: "approved", label: "Approved" },
  completed: { cls: "completed", label: "Completed" },
  rejected: { cls: "rejected", label: "Rejected" },
  error: { cls: "error", label: "Error" },
};
function wfStatus(r: RunSummary) {
  if (r.status === "approved" && r.decision?.auto_approve) return { cls: "auto", label: "Auto-approved" };
  return WF_STATUS[r.status] ?? { cls: "running", label: r.status };
}
function wfTitle(r: RunSummary): string {
  const d = r.decision;
  return d?.order_id ? `Refund ${usd(d.amount_usd)} · order #${d.order_id}` : "Refund run";
}

type Stage = { icon: "ok" | "warn" | "act" | "bad"; sym: string; title: string; body: React.ReactNode };
type AuditRow = { time: string; step: string; detail: React.ReactNode; who: string; human: boolean };

function wfStages(run: RunSummary): Stage[] {
  const d = run.decision;
  const s: Stage[] = [{ icon: "ok", sym: "✓", title: "Who's asking", body: <>{run.requester_name} · verified via <b>Microsoft Entra ID</b></> }];
  s.push(d?.found
    ? { icon: "ok", sym: "✓", title: "Gather the facts", body: <>Order <b>#{d.order_id}</b> · amount <b>{usd(d.amount_usd)}</b> · age <b>{d.order_age_days} days</b>{d.customer ? <> · customer {d.customer}</> : null} · policy <b>refund-policy</b></> }
    : { icon: "warn", sym: "!", title: "Gather the facts", body: <>Order not found for this request.</> });
  if (d?.auto_approve) {
    s.push({ icon: "ok", sym: "✓", title: "Check the rules", body: <>Within policy — amount ≤ <b>{usd(d.policy_limit_usd)}</b> and age ≤ <b>{d.policy_limit_days} days</b>.</> });
  } else if (d) {
    const parts: string[] = [];
    if (d.amount_usd != null && d.policy_limit_usd != null && d.amount_usd > d.policy_limit_usd) parts.push(`${usd(d.amount_usd)} > ${usd(d.policy_limit_usd)}`);
    if (d.order_age_days != null && d.policy_limit_days != null && d.order_age_days > d.policy_limit_days) parts.push(`${d.order_age_days} days > ${d.policy_limit_days} days`);
    s.push({ icon: "warn", sym: "!", title: "Check the rules", body: <>Auto-approve allowed only when amount ≤ <b>{usd(d.policy_limit_usd)}</b> and age ≤ <b>{d.policy_limit_days} days</b>.<br /><span className="rule-hit">Rule hit</span>{parts.join(" · ")} → not eligible for auto-approval.</> });
  } else {
    s.push({ icon: "warn", sym: "!", title: "Check the rules", body: <>{run.status}</> });
  }
  if (run.status === "pending_approval") s.push({ icon: "act", sym: "→", title: "Decision", body: <>Hold the action. Route to <b>{run.approver_name ?? "a manager"}</b> for approval before anything is issued.</> });
  else if (run.status === "approved" || run.status === "completed") s.push({ icon: "ok", sym: "✓", title: "Decision", body: d?.auto_approve ? <>Auto-approved within policy. Refund issued and recorded.</> : <>Approved{run.approver_name ? <> by <b>{run.approver_name}</b></> : null}. Refund issued and recorded.</> });
  else if (run.status === "rejected") s.push({ icon: "bad", sym: "✕", title: "Decision", body: <>Rejected{run.approver_name ? <> by <b>{run.approver_name}</b></> : null} — no refund issued.</> });
  else if (run.status === "error") s.push({ icon: "bad", sym: "✕", title: "Decision", body: <>The run stopped on an error.</> });
  else s.push({ icon: "act", sym: "→", title: "Decision", body: <>In progress…</> });
  return s;
}

function convStages(c: ConvRunDetail): Stage[] {
  const last = c.turns[c.turns.length - 1];
  const sources = last?.answer.citations.length ?? 0;
  const asker = c.asker ?? `via ${surfaceLabel(c.surface)}`;
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  return [
    { icon: "ok", sym: "✓", title: "Who's asking", body: <>{asker} · verified via <b>Microsoft Entra ID</b> · via {surfaceLabel(c.surface)}</> },
    { icon: "ok", sym: "✓", title: "Gather the facts", body: <>Retrieved <b>{plural(sources, "source")}</b> · ACL-rechecked · ranked for this user.</> },
    { icon: "ok", sym: "✓", title: "Ground the answer", body: <>Grounded answer with <b>{plural(sources, "citation")}</b> · nothing out of corpus refused.</> },
    { icon: "ok", sym: "✓", title: "Answered", body: <>Delivered to <b>{surfaceLabel(c.surface)}</b> · {plural(c.turns.length, "turn")}.</> },
  ];
}

function convAudit(c: ConvRunDetail): AuditRow[] {
  const who = c.asker ?? surfaceLabel(c.surface);
  const rows: AuditRow[] = [];
  c.turns.forEach((t) => {
    const n = t.answer.citations.length;
    rows.push({ time: fmtTime(t.ts), step: "Asked", detail: <>&ldquo;{snippet(t.query)}&rdquo;</>, who, human: !!c.asker });
    rows.push({ time: fmtTime(t.ts), step: "Answered", detail: <>&ldquo;{snippet(t.answer.text)}&rdquo;{n ? ` · ${n} source${n === 1 ? "" : "s"}` : ""}</>, who: "SubstrateOS", human: false });
  });
  return rows;
}

type RunItem = { kind: "conversation" | "workflow"; id: string; title: string; sub: string; trigger: string; cls: string; label: string; ts: string };

export default function AdminRunsPage() {
  const [items, setItems] = useState<RunItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [conv, setConv] = useState<ConvRunDetail | null>(null);
  const [wf, setWf] = useState<RunDetail | null>(null);
  const [kind, setKind] = useState<"conversation" | "workflow" | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    Promise.all([getConversationRuns(), getRuns()]).then(([convs, runs]) => {
      const merged: RunItem[] = [
        ...convs.map((c: ConvRunSummary): RunItem => ({
          kind: "conversation", id: c.id, title: c.title || "Conversation",
          sub: `conversation · ${c.surface}`, trigger: surfaceLabel(c.surface),
          cls: "auto", label: "Answered", ts: c.updated_at,
        })),
        ...runs.map((r: RunSummary): RunItem => {
          const sm = wfStatus(r);
          return { kind: "workflow", id: r.id, title: wfTitle(r), sub: "refund playbook · slack", trigger: `${r.requester_name} · Slack`, cls: sm.cls, label: sm.label, ts: r.created_at };
        }),
      ].sort((a, b) => (a.ts < b.ts ? 1 : -1));
      setItems(merged);
      if (merged.length) select(merged[0]);
    }).catch(() => setErr(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = (it: RunItem) => {
    setSelectedId(it.id); setKind(it.kind); setConv(null); setWf(null);
    if (it.kind === "conversation") getConversationRun(it.id).then(setConv);
    else getRun(it.id).then(setWf);
  };

  return (
    <div className="admin-page">
      <div className="admin-wrap">
        <header className="admin-head">
          <h1>Runs</h1>
          <p>Every playbook execution and conversation — the live flow, and the full audit trail behind it.</p>
        </header>
        {err && <div className="admin-note">Couldn&apos;t load runs. Check the admin key / API.</div>}

        {items.length === 0 && !err ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--ink-faint)", fontSize: 14 }}>
            No runs yet — conversations and playbook executions (web, Slack, Teams) will appear here.
          </div>
        ) : (
          <>
            <div className="runs-list">
              {items.map((it) => (
                <div key={`${it.kind}:${it.id}`} className={`run-row${selectedId === it.id ? " active" : ""}`} onClick={() => select(it)}>
                  <div className="pb">{it.title}<span>{it.sub}</span></div>
                  <div className="trig">{it.trigger}</div>
                  <span className={`rst ${it.cls}`}>{it.label}</span>
                  <span className="time">{fmtClock(it.ts)}</span>
                </div>
              ))}
            </div>

            {kind === "conversation" && conv && (
              <div className="run-detail">
                <div className="run-eyebrow">Conversation</div>
                <h2 className="run-title">{conv.title}</h2>
                <p className="run-meta">{surfaceLabel(conv.surface)} · conversation #{conv.id.slice(0, 8)} · {fmtClock(conv.updated_at)}</p>
                <div className="flow-card">
                  <div className="flow-head"><span><b>Grounded Q&amp;A</b> · {surfaceLabel(conv.surface).toLowerCase()} · {conv.turns.length} turn{conv.turns.length === 1 ? "" : "s"}</span><span className="flow-badge ok">Answered</span></div>
                  {convStages(conv).map((s, i) => (
                    <div className="flow-step" key={i}><div className={`flow-ic ${s.icon}`}>{s.sym}</div><div><h4>{s.title}</h4><p>{s.body}</p></div></div>
                  ))}
                </div>
                <div className="audit-wrap">
                  <div className="audit-eyebrow">Audit log</div>
                  <h3>Conversation #{conv.id.slice(0, 8)}</h3>
                  <p className="sub">A complete, tamper-evident record of every turn — nothing is a black box.</p>
                  <table className="audit-table">
                    <thead><tr><th>Time</th><th>Step</th><th>Detail</th><th>Who</th></tr></thead>
                    <tbody>
                      {convAudit(conv).map((row, i) => (
                        <tr key={i}><td className="a-time">{row.time}</td><td className="a-step">{row.step}</td><td className="a-detail">{row.detail}</td><td className="a-who">{row.who}{row.human ? <span className="chip-entra">Entra</span> : null}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {kind === "workflow" && wf && (
              <div className="run-detail">
                <div className="run-eyebrow">{wf.run.status === "pending_approval" || wf.run.status === "running" ? "Live run" : "Run"}</div>
                <h2 className="run-title">Refund playbook</h2>
                <p className="run-meta">Slack · run #{wf.run.id} · {fmtClock(wf.run.created_at)}</p>
                <div className="flow-card">
                  <div className="flow-head"><span><b>refund_v1</b>{wf.run.approver_name ? <> · owner: {wf.run.approver_name}</> : null}</span><span className="flow-badge">{wfStatus(wf.run).label}</span></div>
                  {wfStages(wf.run).map((s, i) => (
                    <div className="flow-step" key={i}><div className={`flow-ic ${s.icon}`}>{s.sym}</div><div><h4>{s.title}</h4><p>{s.body}</p></div></div>
                  ))}
                </div>
                <div className="audit-wrap">
                  <div className="audit-eyebrow">Audit log</div>
                  <h3>Refund playbook · run #{wf.run.id}</h3>
                  <p className="sub">A complete, tamper-evident record of every step — nothing is a black box.</p>
                  <table className="audit-table">
                    <thead><tr><th>Time</th><th>Step</th><th>Detail</th><th>Who</th></tr></thead>
                    <tbody>
                      {wf.events.map((e, i) => (
                        <tr key={i}><td className="a-time">{fmtTime(e.ts)}</td><td className="a-step">{e.step}</td><td className="a-detail">{e.detail}</td><td className="a-who">{e.actor}{e.actor === wf.run.requester_name || e.actor === wf.run.approver_name ? <span className="chip-entra">Entra</span> : null}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
