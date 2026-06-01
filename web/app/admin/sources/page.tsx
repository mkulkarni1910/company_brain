"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { getConnections, getSites, connectSite, resync, disconnect, getJob,
         Connection, SiteOption, SyncJob } from "@/lib/adminApi";

type Provider = {
  key: string; name: string;
  logo?: string;                       // file in /public/logos
  tile?: { bg: string; text: string }; // fallback when no logo exists
  note?: string;                       // subtitle for the connectable one
  connectable?: boolean;
};
type Category = { label: string; providers: Provider[] };

// Only SharePoint is wired today; the rest are catalog placeholders ("Soon").
const CATALOG: Category[] = [
  { label: "Document Repository", providers: [
    { key: "sharepoint", name: "SharePoint", logo: "sharepoint.svg", note: "Sites & document libraries", connectable: true },
    { key: "google-drive", name: "Google Drive", logo: "google-drive.svg" },
    { key: "box", name: "Box", logo: "box.svg" },
    { key: "dropbox", name: "Dropbox", logo: "dropbox.svg" },
  ]},
  { label: "Messaging", providers: [
    { key: "teams", name: "Microsoft Teams", logo: "teams.svg" },
    { key: "slack", name: "Slack", logo: "slack.svg" },
    { key: "google-chat", name: "Google Chat", logo: "google-chat.svg" },
  ]},
  { label: "Wiki / Knowledge", providers: [
    { key: "confluence", name: "Confluence", logo: "confluence.svg" },
    { key: "notion", name: "Notion", logo: "notion.svg" },
    { key: "coda", name: "Coda", logo: "coda.svg" },
  ]},
  { label: "CRM", providers: [
    { key: "salesforce", name: "Salesforce", logo: "salesforce.svg" },
    { key: "hubspot", name: "HubSpot", logo: "hubspot.svg" },
    { key: "dynamics", name: "Microsoft Dynamics", tile: { bg: "linear-gradient(135deg,#0a4c8b,#002050)", text: "D365" } },
  ]},
  { label: "Tickets / Issues", providers: [
    { key: "jira", name: "Jira", logo: "jira.svg" },
    { key: "linear", name: "Linear", logo: "linear.svg" },
    { key: "servicenow", name: "ServiceNow", logo: "servicenow.svg" },
  ]},
  { label: "Meetings", providers: [
    { key: "teams-mtg", name: "Microsoft Teams", logo: "teams.svg" },
    { key: "zoom", name: "Zoom", logo: "zoom.svg" },
    { key: "google-meet", name: "Google Meet", logo: "google-meet.svg" },
  ]},
  { label: "Email", providers: [
    { key: "outlook", name: "Outlook", logo: "outlook.svg" },
    { key: "gmail", name: "Gmail", logo: "gmail.svg" },
  ]},
  { label: "Calendar", providers: [
    { key: "gcal", name: "Google Calendar", logo: "google-calendar.svg" },
    { key: "outlook-cal", name: "Outlook", logo: "outlook.svg" },
  ]},
];

function ProviderCard({ p, onConnect }: { p: Provider; onConnect: () => void }) {
  const icon = (
    <span className="pi">
      {p.logo
        ? <img src={`/logos/${p.logo}`} alt="" width={26} height={26} loading="lazy" />
        : <span className="pi-tile" style={{ background: p.tile?.bg }}>{p.tile?.text}</span>}
    </span>
  );
  const meta = (
    <span className="pmeta"><b>{p.name}</b><span>{p.connectable ? (p.note ?? "Connect") : "Soon"}</span></span>
  );
  return p.connectable
    ? <button className="prov-card" onClick={onConnect}>{icon}{meta}</button>
    : <div className="prov-card soon" aria-disabled="true">{icon}{meta}</div>;
}

export default function DataSources() {
  const [conns, setConns] = useState<Connection[]>([]);
  const [picking, setPicking] = useState(false);
  const [sites, setSites] = useState<SiteOption[] | null>(null);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const aliveRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { aliveRef.current = false; if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const refresh = useCallback(() => { getConnections().then(setConns).catch(() => {}); }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const openPicker = () => { setPicking(true); setSites(null); getSites().then(setSites).catch(() => setSites([])); };

  const poll = useCallback((id: string) => {
    const tick = async () => {
      if (!aliveRef.current) return;
      try {
        const j = await getJob(id);
        if (!aliveRef.current) return;
        setJob(j);
        if (["succeeded", "failed", "unknown"].includes(j.status)) { refresh(); setBusyId(null); return; }
      } catch { /* keep trying */ }
      if (aliveRef.current) timerRef.current = setTimeout(tick, 2000);
    };
    tick();
  }, [refresh]);

  const onConnect = async (s: SiteOption) => {
    setPicking(false); setBusyId("new");
    try {
      const r = await connectSite(s); refresh(); poll(r.connection_id);
    } catch { setBusyId(null); }
  };
  const onResync = async (id: string) => {
    setBusyId(id);
    try { await resync(id); poll(id); }
    catch { setBusyId(null); }
  };
  const onDisconnect = async (id: string) => { await disconnect(id); refresh(); };

  return (
    <div className="admin-page">
      <header className="admin-head"><h1>Data Sources</h1>
        <p>Connect SharePoint to bring its files into the intelligence layer.</p></header>

      {CATALOG.map((cat) => (
        <section className="prov-cat" key={cat.label}>
          <h2 className="cat-label">{cat.label}</h2>
          <div className="prov-grid">
            {cat.providers.map((p) => (
              <ProviderCard key={p.key} p={p} onConnect={openPicker} />
            ))}
          </div>
        </section>
      ))}

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
            <div className="modal-foot">
              <button className="modal-close" onClick={() => setPicking(false)}>Cancel</button>
            </div>
          </div>
        </div>)}
    </div>
  );
}
