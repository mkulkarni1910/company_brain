"use client";
import { useEffect, useState, useCallback } from "react";
import { getConnections, getSites, connectSite, resync, disconnect, getJob,
         Connection, SiteOption, SyncJob } from "@/lib/adminApi";

export default function DataSources() {
  const [conns, setConns] = useState<Connection[]>([]);
  const [picking, setPicking] = useState(false);
  const [sites, setSites] = useState<SiteOption[] | null>(null);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(() => { getConnections().then(setConns).catch(() => {}); }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const openPicker = () => { setPicking(true); setSites(null); getSites().then(setSites).catch(() => setSites([])); };

  const poll = useCallback((id: string) => {
    const tick = async () => {
      try {
        const j = await getJob(id); setJob(j);
        if (["succeeded", "failed", "unknown"].includes(j.status)) { refresh(); setBusyId(null); return; }
      } catch { /* keep trying */ }
      setTimeout(tick, 2000);
    };
    tick();
  }, [refresh]);

  const onConnect = async (s: SiteOption) => {
    setPicking(false); setBusyId("new");
    const r = await connectSite(s); refresh(); poll(r.connection_id);
  };
  const onResync = async (id: string) => { setBusyId(id); await resync(id); poll(id); };
  const onDisconnect = async (id: string) => { await disconnect(id); refresh(); };

  return (
    <div className="admin-page">
      <header className="admin-head"><h1>Data Sources</h1>
        <p>Connect SharePoint to bring its files into the intelligence layer.</p></header>

      <div className="connect-row">
        <button className="connect-card" onClick={openPicker}>
          <div className="ci sp">SP</div><div><b>SharePoint</b><span>Sites &amp; document libraries</span></div></button>
        <div className="connect-card soon"><div className="ci">OD</div><div><b>OneDrive</b><span>Soon</span></div></div>
        <div className="connect-card soon"><div className="ci">TM</div><div><b>Teams</b><span>Soon</span></div></div>
      </div>

      {busyId && job && (
        <div className="card sync-status">
          <b>Syncing…</b> {job.processed}/{job.total} indexed · {job.skipped} skipped
          {job.errors ? ` · ${job.errors} errors` : ""}{job.truncated ? " · truncated" : ""}
          <div className="bar"><span style={{ width: `${job.total ? (100 * (job.processed + job.skipped)) / job.total : 0}%` }} /></div>
        </div>)}

      <section className="card">
        <h3>Connected sources</h3>
        {conns.length === 0 && <p className="muted">Nothing connected yet.</p>}
        <table className="conn-table"><tbody>
          {conns.map((c) => (
            <tr key={c.connection_id}>
              <td><b>{c.name}</b><span className="sub2">{c.type}</span></td>
              <td><span className={`pill ${c.status}`}>{c.status}</span></td>
              <td>{c.item_count} items</td>
              <td className="actions">
                <button onClick={() => onResync(c.connection_id)} disabled={busyId === c.connection_id}>Sync</button>
                <button className="danger" onClick={() => onDisconnect(c.connection_id)}>Disconnect</button>
              </td>
            </tr>))}
        </tbody></table>
      </section>

      {picking && (
        <div className="admin-modal" onClick={() => setPicking(false)}>
          <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>Connect a SharePoint site</h3>
            {sites === null && <p className="muted">Loading sites…</p>}
            {sites !== null && sites.length === 0 && (
              <p className="muted">No sites available. Connecting is blocked until the
                <b> Sites.Read.All</b> Graph permission is consented on the SubStrateOS app.</p>)}
            {(sites ?? []).map((s) => (
              <button key={s.site_id} className="site-row" onClick={() => onConnect(s)}>
                <b>{s.name}</b><span>{s.web_url}</span></button>))}
            <button className="modal-close" onClick={() => setPicking(false)}>Cancel</button>
          </div>
        </div>)}
    </div>
  );
}
