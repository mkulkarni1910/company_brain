"use client";
import { Fragment, useEffect, useState } from "react";
import { getRun, getRuns, RunDetail, RunSummary } from "@/lib/runsApi";

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  pending_approval: { bg: "#F5E6D0", fg: "#8a5a12" },
  approved: { bg: "#D8F0E4", fg: "#136345" },
  completed: { bg: "#D8F0E4", fg: "#136345" },
  rejected: { bg: "#FBE3E4", fg: "#8a1f2b" },
  running: { bg: "#E7EEFB", fg: "#1b4fae" },
  error: { bg: "#FBE3E4", fg: "#8a1f2b" },
};

function StatusPill({ status }: { status: string }) {
  const c = STATUS_COLORS[status] ?? { bg: "#eee", fg: "#444" };
  return (
    <span style={{
      background: c.bg, color: c.fg, borderRadius: 12, padding: "2px 10px",
      fontSize: 11, fontWeight: 700, letterSpacing: ".02em", whiteSpace: "nowrap",
    }}>
      {status.replace("_", " ")}
    </span>
  );
}

function usd(v: number | null | undefined): string {
  return v == null ? "—" : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function AuditTable({ detail }: { detail: RunDetail }) {
  return (
    <div style={{ margin: "12px 0 24px" }}>
      <table className="skills-table">
        <thead>
          <tr><th>Time</th><th>Step</th><th>Detail</th><th>Who</th></tr>
        </thead>
        <tbody>
          {detail.events.map((e, i) => (
            <tr key={i}>
              <td style={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                {new Date(e.ts).toLocaleTimeString()}
              </td>
              <td style={{ fontWeight: 600 }}>{e.step}</td>
              <td>{e.detail}</td>
              <td style={{ whiteSpace: "nowrap" }}>{e.actor}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);

  useEffect(() => { getRuns().then(setRuns); }, []);
  useEffect(() => {
    if (!open) { setDetail(null); return; }
    getRun(open).then(setDetail);
  }, [open]);

  return (
    <main className="main">
      <div style={{ padding: "0 28px" }}>
        <div className="skills-page">
          <div className="skills-header">
            <h1>Runs</h1>
            <p>Every workflow run, on the record — who asked, which rule fired, who approved.</p>
          </div>
          {runs.length === 0 && <div className="skills-empty">No runs yet.</div>}
          {runs.length > 0 && (
            <table className="skills-table">
              <thead>
                <tr><th>Run</th><th>Status</th><th>Requested by</th><th>Customer</th>
                    <th>Order</th><th>Amount</th><th>Approver</th><th>When</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <Fragment key={r.id}>
                    <tr onClick={() => setOpen(open === r.id ? null : r.id)}
                        style={{ cursor: "pointer" }}>
                      <td style={{ fontWeight: 600 }}>{r.id}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td>{r.requester_name}</td>
                      <td>{r.decision?.customer ?? "—"}</td>
                      <td>{r.decision?.order_id ? `#${r.decision.order_id}` : "—"}</td>
                      <td>{usd(r.decision?.amount_usd)}</td>
                      <td>{r.approver_name ?? "—"}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {new Date(r.created_at).toLocaleString()}
                      </td>
                    </tr>
                    {open === r.id && detail && (
                      <tr>
                        <td colSpan={8}><AuditTable detail={detail} /></td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </main>
  );
}
