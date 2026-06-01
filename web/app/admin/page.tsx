"use client";
import { useEffect, useState } from "react";
import { getStats, AdminStats } from "@/lib/adminApi";

const fmt = (n: number | null) => (n === null || n === undefined ? "—" : n.toLocaleString());

export default function Overview() {
  const [s, setS] = useState<AdminStats | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { getStats().then(setS).catch(() => setErr(true)); }, []);
  return (
    <div className="admin-page">
      <header className="admin-head"><h1>Overview</h1>
        <p>Your work context layer at a glance.</p></header>
      {err && <div className="admin-note">Couldn&apos;t load stats. Check the admin key / API.</div>}
      <div className="tiles">
        <Tile label="Active users" value={fmt(s?.active_users ?? null)} hint="last 7d" />
        <Tile label="Sources live" value={fmt(s?.sources_live ?? null)} hint="connected" />
        <Tile label="Items indexed" value={fmt(s?.items_indexed ?? null)} hint="chunks" />
        <Tile label="Queries · 7d" value={fmt(s?.queries_7d ?? null)} hint="total" />
      </div>
      <div className="admin-cols">
        <section className="card">
          <h3>Needs attention</h3>
          {(s?.needs_attention ?? []).length === 0 && <p className="muted">All clear.</p>}
          {(s?.needs_attention ?? []).map((n, i) => (
            <div className="attn" key={i}><span className="dot amber" /><div>{n.text}</div>
              <span className="where">{n.where}</span></div>))}
        </section>
        <section className="card">
          <h3>Source health</h3>
          {(s?.source_health ?? []).length === 0 && <p className="muted">No sources yet.</p>}
          {(s?.source_health ?? []).map((h, i) => (
            <div className="health" key={i}>
              <span className={`dot ${h.status === "live" ? "green" : h.status === "error" ? "rose" : "amber"}`} />
              <div className="hn">{h.name}<span>{h.items} items</span></div>
              <span className="status">{h.status}</span></div>))}
        </section>
      </div>
      <section className="card">
        <h3>Recent activity</h3>
        {(s?.recent_activity ?? []).length === 0 && <p className="muted">No activity yet.</p>}
        {(s?.recent_activity ?? []).map((a, i) => (
          <div className="activity" key={i}><span className="dot green" /><div>{a.text}</div>
            <span className="who-when">{a.actor}</span></div>))}
      </section>
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (<div className="tile"><div className="tile-label">{label}</div>
    <div className="tile-value">{value}</div><div className="tile-hint">{hint}</div></div>);
}
