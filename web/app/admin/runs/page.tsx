"use client";
import { useEffect, useState } from "react";
import { getRuns, getRun, RunSummary, RunDetail } from "@/lib/runsApi";

function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("en-US", { hour12: false }); }
  catch { return iso; }
}
function fmtClock(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }); }
  catch { return iso; }
}
function usd(v: number | null | undefined): string {
  return v == null ? "—" : `$${v.toLocaleString("en-US")}`;
}

const STATUS_META: Record<string, { cls: string; label: string }> = {
  pending_approval: { cls: "stopped", label: "Stopped for approval" },
  running: { cls: "running", label: "Running" },
  approved: { cls: "approved", label: "Approved" },
  completed: { cls: "completed", label: "Completed" },
  rejected: { cls: "rejected", label: "Rejected" },
  error: { cls: "error", label: "Error" },
};
function statusMeta(r: RunSummary) {
  const m = STATUS_META[r.status] ?? { cls: "running", label: r.status };
  if (r.status === "approved" && r.decision?.auto_approve) return { cls: "auto", label: "Auto-approved" };
  return m;
}

type Stage = { icon: "ok" | "warn" | "act" | "bad"; sym: string; title: string; body: React.ReactNode };

function buildStages(run: RunSummary): Stage[] {
  const d = run.decision;
  const stages: Stage[] = [];

  stages.push({
    icon: "ok", sym: "✓", title: "Who's asking",
    body: <>{run.requester_name} · verified via <b>Microsoft Entra ID</b></>,
  });

  if (d?.found) {
    stages.push({
      icon: "ok", sym: "✓", title: "Gather the facts",
      body: <>Order <b>#{d.order_id}</b> · amount <b>{usd(d.amount_usd)}</b> · age <b>{d.order_age_days} days</b>
        {d.customer ? <> · customer {d.customer}</> : null} · policy <b>refund-policy</b></>,
    });
  } else {
    stages.push({ icon: "warn", sym: "!", title: "Gather the facts", body: <>Order not found for this request.</> });
  }

  if (d?.auto_approve) {
    stages.push({
      icon: "ok", sym: "✓", title: "Check the rules",
      body: <>Within policy — amount ≤ <b>{usd(d.policy_limit_usd)}</b> and age ≤ <b>{d.policy_limit_days} days</b>. Eligible for auto-approval.</>,
    });
  } else if (d) {
    const overAmt = d.amount_usd != null && d.policy_limit_usd != null && d.amount_usd > d.policy_limit_usd;
    const overAge = d.order_age_days != null && d.policy_limit_days != null && d.order_age_days > d.policy_limit_days;
    const parts: string[] = [];
    if (overAmt) parts.push(`${usd(d.amount_usd)} > ${usd(d.policy_limit_usd)}`);
    if (overAge) parts.push(`${d.order_age_days} days > ${d.policy_limit_days} days`);
    stages.push({
      icon: "warn", sym: "!", title: "Check the rules",
      body: <>Auto-approve allowed only when amount ≤ <b>{usd(d.policy_limit_usd)}</b> and age ≤ <b>{d.policy_limit_days} days</b>.<br />
        <span className="rule-hit">Rule hit</span>{parts.join(" · ")} → not eligible for auto-approval.</>,
    });
  } else {
    stages.push({ icon: "warn", sym: "!", title: "Check the rules", body: <>{run.status}</> });
  }

  if (run.status === "pending_approval") {
    stages.push({ icon: "act", sym: "→", title: "Decision",
      body: <>Hold the action. Route to <b>{run.approver_name ?? "a manager"}</b> for approval before anything is issued.</> });
  } else if (run.status === "approved" || run.status === "completed") {
    stages.push({ icon: "ok", sym: "✓", title: "Decision",
      body: d?.auto_approve
        ? <>Auto-approved within policy. Refund issued and recorded.</>
        : <>Approved{run.approver_name ? <> by <b>{run.approver_name}</b></> : null}. Refund issued and recorded.</> });
  } else if (run.status === "rejected") {
    stages.push({ icon: "bad", sym: "✕", title: "Decision", body: <>Rejected{run.approver_name ? <> by <b>{run.approver_name}</b></> : null} — no refund issued.</> });
  } else if (run.status === "error") {
    stages.push({ icon: "bad", sym: "✕", title: "Decision", body: <>The run stopped on an error.</> });
  } else {
    stages.push({ icon: "act", sym: "→", title: "Decision", body: <>In progress…</> });
  }
  return stages;
}

export default function AdminRunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getRuns().then((rs) => {
      setRuns(rs);
      if (rs.length) { setSelectedId(rs[0].id); getRun(rs[0].id).then(setDetail); }
    }).catch(() => setErr(true));
  }, []);

  const select = (id: string) => {
    setSelectedId(id);
    setDetail(null);
    getRun(id).then(setDetail);
  };

  const run = detail?.run;
  const isHuman = (actor: string) => !!run && (actor === run.requester_name || actor === run.approver_name);

  return (
    <div className="admin-page">
      <div className="admin-wrap">
        <header className="admin-head">
          <h1>Runs</h1>
          <p>Every playbook execution — the live flow, and the full audit trail behind it.</p>
        </header>
        {err && <div className="admin-note">Couldn&apos;t load runs. Check the admin key / API.</div>}

        {runs.length === 0 && !err ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--ink-faint)", fontSize: 14 }}>
            No runs yet — playbook executions (e.g. a Slack refund) will appear here.
          </div>
        ) : (
          <>
            <div className="runs-list">
              {runs.map((r) => {
                const sm = statusMeta(r);
                return (
                  <div key={r.id} className={`run-row${selectedId === r.id ? " active" : ""}`} onClick={() => select(r.id)}>
                    <div className="pb">Refund playbook<span>refund_v1 · #{r.id}</span></div>
                    <div className="trig">Triggered from Slack</div>
                    <span className={`rst ${sm.cls}`}>{sm.label}</span>
                    <span className="time">{fmtClock(r.created_at)}</span>
                  </div>
                );
              })}
            </div>

            {run && (
              <div className="run-detail">
                <div className="run-eyebrow">{run.status === "pending_approval" || run.status === "running" ? "Live run" : "Run"}</div>
                <h2 className="run-title">Refund playbook</h2>
                <p className="run-meta">Triggered from Slack · run #{run.id} · {fmtClock(run.created_at)}</p>

                <div className="flow-card">
                  <div className="flow-head">
                    <span><b>refund_v1</b>{run.approver_name ? <> · owner: {run.approver_name}</> : null}</span>
                    <span className="flow-badge">{statusMeta(run).label}</span>
                  </div>
                  {buildStages(run).map((s, i) => (
                    <div className="flow-step" key={i}>
                      <div className={`flow-ic ${s.icon}`}>{s.sym}</div>
                      <div><h4>{s.title}</h4><p>{s.body}</p></div>
                    </div>
                  ))}
                </div>

                <div className="audit-wrap">
                  <div className="audit-eyebrow">Audit log</div>
                  <h3>Refund playbook · run #{run.id}</h3>
                  <p className="sub">A complete, tamper-evident record of every step — nothing is a black box.</p>
                  <table className="audit-table">
                    <thead><tr><th>Time</th><th>Step</th><th>Detail</th><th>Who</th></tr></thead>
                    <tbody>
                      {(detail?.events ?? []).map((e, i) => (
                        <tr key={i}>
                          <td className="a-time">{fmtTime(e.ts)}</td>
                          <td className="a-step">{e.step}</td>
                          <td className="a-detail">{e.detail}</td>
                          <td className="a-who">{e.actor}{isHuman(e.actor) ? <span className="chip-entra">Entra</span> : null}</td>
                        </tr>
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
