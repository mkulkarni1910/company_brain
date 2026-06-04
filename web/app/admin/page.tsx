"use client";
import { useEffect, useState } from "react";
import { getStats, AdminStats } from "@/lib/adminApi";

const fmt = (n: number | null) => (n === null || n === undefined ? "—" : n.toLocaleString());

function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (isNaN(d)) return "—";
  const diff = Math.floor((Date.now() - d) / 1000);
  if (diff < 5) return "now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function Overview() {
  const [s, setS] = useState<AdminStats | null>(null);
  const [err, setErr] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    getStats()
      .then(setS)
      .catch(() => setErr(true))
      .finally(() => setLoading(false));
  }, []);
  return (
    <div className="admin-page">
      <header className="admin-head">
        <h1>Overview</h1>
        <p>Your work context layer at a glance.</p>
      </header>
      {err && <div className="admin-note">Couldn&apos;t load stats. Check the admin key / API.</div>}
      <div className="tiles">
        <Tile label="Active users" value={fmt(s?.active_users ?? null)} />
        <Tile label="Sources live" value={fmt(s?.sources_live ?? null)} />
        <Tile label="Items indexed" value={fmt(s?.items_indexed ?? null)} />
        <Tile label="Queries · 7d" value={fmt(s?.queries_7d ?? null)} />
      </div>
      <section className="card">
        <h3>Needs attention</h3>
        {loading && <p className="muted">Loading…</p>}
        {!loading && (s?.needs_attention ?? []).length === 0 && <p className="muted">All clear.</p>}
        {(s?.needs_attention ?? []).map((n, i) => (
          <div className="attn" key={`${n.where}-${i}`}>
            <span className={`dot ${n.severity === "error" ? "rose" : n.severity === "ok" ? "green" : "amber"}`} />
            <div>{n.text}</div>
            <span className="where">{n.where} ›</span>
          </div>
        ))}
      </section>
      <section className="card">
        <h3>Recent activity</h3>
        {loading && <p className="muted">Loading…</p>}
        {!loading && (s?.recent_activity ?? []).length === 0 && <p className="muted">No activity yet.</p>}
        {(s?.recent_activity ?? []).map((a, i) => (
          <div className="activity" key={`${a.ts}-${a.actor}-${i}`}>
            <span className="dot amber" style={{ marginTop: 5, flexShrink: 0 }} />
            <div className="activity-body">
              <div className="activity-text">{a.text}</div>
              <div className="activity-meta">{a.actor} · {relTime(a.ts)}</div>
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
    </div>
  );
}
