"use client";
import { useEffect, useState } from "react";
import { getRuns, getRun, RunSummary, RunDetail, AuditEvent } from "@/lib/runsApi";
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
  pending_confirm: { cls: "stopped", label: "Awaiting confirm" },
  running: { cls: "running", label: "Running" },
  approved: { cls: "approved", label: "Approved" },
  completed: { cls: "completed", label: "Completed" },
  rejected: { cls: "rejected", label: "Rejected" },
  cancelled: { cls: "error", label: "Cancelled" },
  error: { cls: "error", label: "Error" },
  needs_attention: { cls: "error", label: "Needs attention" },
  routed_to_support: { cls: "stopped", label: "Routed to support" },
};
function wfStatus(r: RunSummary) {
  if (r.status === "approved" && r.decision?.auto_approve) return { cls: "auto", label: "Auto-approved" };
  return WF_STATUS[r.status] ?? { cls: "running", label: r.status };
}
function wfTitle(r: RunSummary): string {
  if (r.kind === "approval") return snippet(r.request_text || "Approval request", 60);
  if (r.kind === "github_pr") return snippet(r.pr_draft?.title || r.request_text || "PR request", 60);
  const d = r.decision;
  return d?.order_id ? `Refund ${usd(d.amount_usd)} · order #${d.order_id}` : "Refund run";
}
const isApproval = (r: RunSummary) => r.kind === "approval";
const isGithubPr = (r: RunSummary) => r.kind === "github_pr";

function approvalStages(run: RunSummary): Stage[] {
  const s: Stage[] = [
    { icon: "ok", sym: "✓", title: "Who's asking", body: <>{run.requester_name} · verified via <b>Microsoft Entra ID</b></> },
    { icon: "ok", sym: "✓", title: "The request", body: <>&ldquo;{run.request_text || "—"}&rdquo;</> },
  ];
  if (run.approver_name) {
    s.push({ icon: "ok", sym: "✓", title: "Find the approver", body: <>Resolved <b>{run.approver_name}</b>{run.approver_source === "manager" ? <> — the requester&rsquo;s manager (Entra <code>manages</code> edge)</> : run.approver_source === "fallback" ? <> — the configured approver</> : null}.</> });
  } else {
    s.push({ icon: "warn", sym: "!", title: "Find the approver", body: <>Couldn&rsquo;t resolve an approver — asked the requester who to route to.</> });
  }
  if (run.status === "pending_approval") s.push({ icon: "act", sym: "→", title: "Decision", body: <>Hold the action. Sent an <b>Approve / Reject</b> card to {run.approver_name}. Awaiting sign-off — nothing acts until they approve.</> });
  else if (run.status === "approved" || run.status === "completed") s.push({ icon: "ok", sym: "✓", title: "Decision", body: <>Approved by <b>{run.approver_name}</b>. The requester was cleared to proceed.</> });
  else if (run.status === "rejected") s.push({ icon: "bad", sym: "✕", title: "Decision", body: <>Rejected by <b>{run.approver_name}</b> — no action taken.</> });
  else if (run.status === "error") s.push({ icon: "bad", sym: "✕", title: "Decision", body: <>Couldn&rsquo;t route this for approval.</> });
  else s.push({ icon: "act", sym: "→", title: "Decision", body: <>In progress…</> });
  return s;
}

function githubStages(run: RunSummary): Stage[] {
  const s: Stage[] = [
    {
      icon: "ok", sym: "✓", title: "Request received",
      body: <>&ldquo;{run.request_text || "—"}&rdquo;{run.surface ? <> · via <b>{surfaceLabel(run.surface)}</b></> : null}</>,
    },
  ];
  if (run.pr_draft) {
    s.push({
      icon: "ok", sym: "✓", title: "Draft the change",
      body: <>{run.pr_draft.path ? <><b>{run.pr_draft.path}</b> · </> : null}{run.pr_draft.summary || "Draft prepared."}</>,
    });
  }
  if (run.status === "pending_confirm") {
    s.push({ icon: "act", sym: "→", title: "Decision", body: <>Preview shown — waiting for the requester to confirm. Nothing reaches GitHub until they do.</> });
  } else if (run.status === "completed") {
    s.push({ icon: "ok", sym: "✓", title: "Decision", body: <>Confirmed by the requester.</> });
  } else if (run.status === "cancelled") {
    s.push({ icon: "bad", sym: "✕", title: "Decision", body: <>Cancelled by the requester — nothing reached GitHub.</> });
  } else if (run.status === "error") {
    s.push({ icon: "bad", sym: "✕", title: "Decision", body: <>The run stopped on an error.</> });
  } else {
    s.push({ icon: "act", sym: "→", title: "Decision", body: <>In progress…</> });
  }
  if (run.status === "completed" && run.pr_url) {
    s.push({
      icon: "ok", sym: "✓", title: "PR created",
      body: <>PR created — authored as the requester. <a href={run.pr_url} target="_blank" rel="noopener noreferrer">View PR ↗</a></>,
    });
  }
  return s;
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

type RunFilter = "all" | "stopped" | "running" | "answered" | "rejected";
type RunItem = { kind: "conversation" | "workflow"; id: string; title: string; sub: string; trigger: string; cls: string; label: string; ts: string; filter: RunFilter; search: string };

// Map a status class to the coarse filter bucket the chips expose.
function filterBucket(cls: string): RunFilter {
  if (cls === "stopped") return "stopped";
  if (cls === "running") return "running";
  if (cls === "rejected" || cls === "error") return "rejected";
  return "answered"; // auto / approved / completed
}

const FILTERS: { key: RunFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "stopped", label: "Stopped" },
  { key: "running", label: "Running" },
  { key: "answered", label: "Answered" },
  { key: "rejected", label: "Rejected" },
];

export default function AdminRunsPage() {
  const [items, setItems] = useState<RunItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [conv, setConv] = useState<ConvRunDetail | null>(null);
  const [wf, setWf] = useState<RunDetail | null>(null);
  const [kind, setKind] = useState<"conversation" | "workflow" | null>(null);
  const [err, setErr] = useState(false);
  const [filter, setFilter] = useState<RunFilter>("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    Promise.all([getConversationRuns(), getRuns()]).then(([convs, runs]) => {
      const merged: RunItem[] = [
        ...convs.map((c: ConvRunSummary): RunItem => ({
          kind: "conversation", id: c.id, title: c.title || "Conversation",
          sub: `conversation · ${surfaceLabel(c.surface)} · ${c.surface}`, trigger: surfaceLabel(c.surface),
          cls: "auto", label: "Answered", ts: c.updated_at, filter: "answered",
          search: `${c.title ?? ""} ${surfaceLabel(c.surface)} ${c.surface}`.toLowerCase(),
        })),
        ...runs.map((r: RunSummary): RunItem => {
          const sm = wfStatus(r);
          const type = isApproval(r) ? "request-approval"
            : isGithubPr(r) ? "raise-pr playbook"
            : "refund playbook";
          return {
            kind: "workflow", id: r.id, title: wfTitle(r), sub: `${type} · ${r.requester_name}`,
            trigger: `${r.requester_name} · Slack`, cls: sm.cls, label: sm.label, ts: r.created_at,
            filter: filterBucket(sm.cls),
            search: `${wfTitle(r)} ${r.requester_name} ${type} ${r.id} ${r.decision?.order_id ?? ""}`.toLowerCase(),
          };
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

  const counts = items.reduce<Record<string, number>>((acc, it) => {
    acc.all = (acc.all ?? 0) + 1;
    acc[it.filter] = (acc[it.filter] ?? 0) + 1;
    return acc;
  }, {});
  const q = query.trim().toLowerCase();
  const shown = items.filter(
    (it) => (filter === "all" || it.filter === filter) && (!q || it.search.includes(q)),
  );

  return (
    <div className="admin-page">
      {/* Governance receipt styles — actor chips, rule badge, approver identity */}
      <style>{`
        .chip-human{display:inline-block;font-family:var(--font-mono),monospace;font-size:9.5px;letter-spacing:.05em;font-weight:600;text-transform:uppercase;background:var(--amber-bg);color:#7a5410;border-radius:5px;padding:2px 7px;margin-left:7px;vertical-align:1px}
        .chip-system{display:inline-block;font-family:var(--font-mono),monospace;font-size:9.5px;letter-spacing:.05em;font-weight:600;text-transform:uppercase;background:#eeebe3;color:var(--ink-faint);border-radius:5px;padding:2px 7px;margin-left:7px;vertical-align:1px}
        .chip-agent{display:inline-block;font-family:var(--font-mono),monospace;font-size:9.5px;letter-spacing:.05em;font-weight:600;text-transform:uppercase;background:#e6edfb;color:#3a5bd0;border-radius:5px;padding:2px 7px;margin-left:7px;vertical-align:1px}
        .rule-badge{display:inline-block;font-family:var(--font-mono),monospace;font-size:10px;letter-spacing:.03em;font-weight:500;background:var(--surface-2);color:var(--ink-dim);border:1px solid var(--line);border-radius:5px;padding:2px 8px;margin-left:7px;vertical-align:1px;white-space:nowrap}
        .approver-id{display:block;font-size:12px;color:var(--ink-faint);margin-top:4px;line-height:1.5}
        .approver-id .ap-name{font-weight:600;color:var(--ink-dim)}
        .approver-id .ap-sep{margin:0 4px;color:var(--line)}
      `}</style>
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
            <div className="runs-toolbar">
              <div className="search run-search">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
                <input
                  placeholder="Search runs, people, orders…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <div className="run-chips">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    className={`run-chip${filter === f.key ? " active" : ""}`}
                    onClick={() => setFilter(f.key)}
                  >
                    {f.label}
                    {counts[f.key] ? <span className="ct">{counts[f.key]}</span> : null}
                  </button>
                ))}
              </div>
            </div>

            <div className="runs-split">
              <aside className="runs-col">
                <div className="runs-col-head"><span>Runs</span><em>{shown.length} shown</em></div>
                {shown.map((it) => (
                  <div key={`${it.kind}:${it.id}`} className={`run-row${selectedId === it.id ? " active" : ""}`} onClick={() => select(it)}>
                    <div className="pb">{it.title}</div>
                    <div className="sub">{it.sub}</div>
                    <div className="row-foot"><span className={`rst ${it.cls}`}>{it.label}</span><span className="time">{fmtClock(it.ts)}</span></div>
                  </div>
                ))}
                {shown.length === 0 && <div className="runs-empty">No runs match this filter.</div>}
              </aside>

              <section className="run-detail-col">
            {!selectedId && (
              <div className="run-placeholder">Select a run to see its flow and audit trail.</div>
            )}
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
                <div className="run-eyebrow">{wf.run.status === "pending_approval" || wf.run.status === "pending_confirm" || wf.run.status === "running" ? "Live run" : "Run"}</div>
                <h2 className="run-title">{isApproval(wf.run) || isGithubPr(wf.run) ? wfTitle(wf.run) : "Refund playbook"}</h2>
                <p className="run-meta">{wf.run.surface ? surfaceLabel(wf.run.surface) : "Slack"} · {isApproval(wf.run) ? "request-approval" : isGithubPr(wf.run) ? "raise-pr playbook" : "refund"} · run #{wf.run.id} · {fmtClock(wf.run.created_at)}</p>
                <div className="flow-card">
                  <div className="flow-head"><span>{isApproval(wf.run) ? <><b>request-approval</b> · routed to manager</> : isGithubPr(wf.run) ? <><b>raise-pr</b> · authored as requester</> : <><b>refund_v1</b>{wf.run.approver_name ? <> · owner: {wf.run.approver_name}</> : null}</>}</span><span className={`flow-badge${["approved", "auto", "completed"].includes(wfStatus(wf.run).cls) ? " ok" : ""}`}>{wfStatus(wf.run).label}</span></div>
                  {(isApproval(wf.run) ? approvalStages(wf.run) : isGithubPr(wf.run) ? githubStages(wf.run) : wfStages(wf.run)).map((s, i) => (
                    <div className="flow-step" key={i}><div className={`flow-ic ${s.icon}`}>{s.sym}</div><div><h4>{s.title}</h4><p>{s.body}</p></div></div>
                  ))}
                </div>
                <div className="audit-wrap">
                  <div className="audit-eyebrow">Audit log</div>
                  <h3>{isApproval(wf.run) ? "request-approval" : isGithubPr(wf.run) ? "raise-pr playbook" : "Refund playbook"} · run #{wf.run.id}</h3>
                  <p className="sub">A complete, identity-stamped record of every step — nothing is a black box.</p>
                  <table className="audit-table">
                    <thead><tr><th>Time</th><th>Step</th><th>Detail</th><th>Who</th></tr></thead>
                    <tbody>
                      {(() => {
                        // Build a mutable copy of audit events to consume in order
                        const remaining: (AuditEvent | null)[] = (wf.audit ?? []).slice();
                        return wf.events.map((e, i) => {
                          // Find and consume the first unconsumed audit event matching this step
                          let auditIdx = -1;
                          for (let j = 0; j < remaining.length; j++) {
                            if (remaining[j] !== null && remaining[j]!.step === e.step) {
                              auditIdx = j;
                              break;
                            }
                          }
                          const ae: AuditEvent | null = auditIdx >= 0 ? remaining[auditIdx]! : null;
                          if (auditIdx >= 0) remaining[auditIdx] = null;

                          // Actor cell: name + type chip + entra chip
                          const actorTypeChip = ae
                            ? <span className={`chip-${ae.actor.type}`}>{ae.actor.type}</span>
                            : null;
                          const showEntraChip = ae
                            ? ae.actor.idp === "entra"
                            : (e.actor === wf.run.requester_name || e.actor === wf.run.approver_name);

                          // Detail cell: original detail + optional rule badge
                          const ruleBadge = ae?.rule
                            ? <span className="rule-badge">{`${ae.rule.id} @ v${ae.rule.version} → ${ae.rule.result}`}</span>
                            : null;

                          // Approver identity line: Approved/Rejected step with a human actor
                          const isDecisionStep = (e.step === "Approved" || e.step === "Rejected");
                          const approverLine = (ae && isDecisionStep && ae.actor.type === "human")
                            ? (
                              <span className="approver-id">
                                <span className="ap-name">{ae.actor.id}</span>
                                {ae.actor.idp === "entra" ? <><span className="ap-sep">·</span><span className="chip-entra">Entra</span></> : null}
                              </span>
                            )
                            : null;

                          return (
                            <tr key={i}>
                              <td className="a-time">{fmtTime(e.ts)}</td>
                              <td className="a-step">{e.step}</td>
                              <td className="a-detail">
                                {e.detail}
                                {ruleBadge}
                                {approverLine}
                              </td>
                              <td className="a-who">
                                {e.actor}
                                {actorTypeChip}
                                {showEntraChip ? <span className="chip-entra">Entra</span> : null}
                              </td>
                            </tr>
                          );
                        });
                      })()}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
              </section>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
