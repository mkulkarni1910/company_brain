"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { getConnections, disconnect, connectProvider, Connection } from "@/lib/adminApi";

// ── Catalog ─────────────────────────────────────────────────────────────────

type Provider = {
  key: string;
  name: string;
  desc: string;
  logo?: string;
  tile?: { bg: string; text: string };
  connectable?: boolean;
  connType?: "sharepoint" | "teams"; // backend Connection.type + OAuth provider slug
};
type Category = { label: string; providers: Provider[] };

const CATALOG: Category[] = [
  {
    label: "Document Repository",
    providers: [
      { key: "sharepoint", name: "SharePoint", desc: "Sites & document libraries", logo: "sharepoint.svg", connectable: true, connType: "sharepoint" },
      { key: "google-drive", name: "Google Drive", desc: "Files & shared drives", logo: "google-drive.svg" },
      { key: "box", name: "Box", desc: "Cloud content", logo: "box.svg" },
      { key: "dropbox", name: "Dropbox", desc: "Files & folders", logo: "dropbox.svg" },
    ],
  },
  {
    label: "Messaging",
    providers: [
      { key: "teams-msg", name: "Microsoft Teams", desc: "Channel messages", logo: "teams.svg", connectable: true, connType: "teams" },
      { key: "slack", name: "Slack", desc: "Channels & DMs", logo: "slack.svg" },
      { key: "google-chat", name: "Google Chat", desc: "Spaces & messages", logo: "google-chat.svg" },
    ],
  },
  {
    label: "Wiki / Knowledge",
    providers: [
      { key: "confluence", name: "Confluence", desc: "Spaces & pages", logo: "confluence.svg" },
      { key: "notion", name: "Notion", desc: "Pages & databases", logo: "notion.svg" },
      { key: "coda", name: "Coda", desc: "Docs & tables", logo: "coda.svg" },
    ],
  },
  {
    label: "CRM",
    providers: [
      { key: "salesforce", name: "Salesforce", desc: "Accounts & opportunities", logo: "salesforce.svg" },
      { key: "hubspot", name: "HubSpot", desc: "Contacts & deals", logo: "hubspot.svg" },
      { key: "dynamics", name: "Microsoft Dynamics", desc: "CRM records", tile: { bg: "linear-gradient(135deg,#0a4c8b,#002050)", text: "D365" } },
    ],
  },
  {
    label: "Tickets / Issues",
    providers: [
      { key: "jira", name: "Jira", desc: "Issues & projects", logo: "jira.svg" },
      { key: "linear", name: "Linear", desc: "Issues & cycles", logo: "linear.svg" },
      { key: "servicenow", name: "ServiceNow", desc: "Incidents & tickets", logo: "servicenow.svg" },
    ],
  },
  {
    label: "Meetings",
    providers: [
      { key: "teams-mtg", name: "Microsoft Teams", desc: "Meeting recordings", logo: "teams.svg" },
      { key: "zoom", name: "Zoom", desc: "Recordings & transcripts", logo: "zoom.svg" },
      { key: "google-meet", name: "Google Meet", desc: "Recordings & notes", logo: "google-meet.svg" },
    ],
  },
  {
    label: "Email",
    providers: [
      { key: "outlook", name: "Outlook", desc: "Mail & threads", logo: "outlook.svg" },
      { key: "gmail", name: "Gmail", desc: "Mail & threads", logo: "gmail.svg" },
    ],
  },
  {
    label: "Calendar",
    providers: [
      { key: "gcal", name: "Google Calendar", desc: "Events", logo: "google-calendar.svg" },
      { key: "outlook-cal", name: "Outlook", desc: "Events", logo: "outlook.svg" },
    ],
  },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function relTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 5) return "now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Status cell ───────────────────────────────────────────────────────────────

function StatusCell({ conn }: { conn: Connection | null }) {
  if (!conn) return <span className="st disabled"><span className="d" />Disabled</span>;
  if (conn.status === "live") {
    return <span className="st live"><span className="d" />Live</span>;
  }
  if (conn.status === "syncing") {
    return <span className="st syncing"><span className="d" />Syncing</span>;
  }
  if (conn.status === "error") {
    return <span className="st error"><span className="d" />Error</span>;
  }
  return <span className="st"><span className="d" />{conn.status}</span>;
}

// ── Toggle ────────────────────────────────────────────────────────────────────

type ToggleProps = {
  connectable: boolean;
  conn: Connection | null;
  onEnable: () => void;
  onDisable: () => void;
};

function Toggle({ connectable, conn, onEnable, onDisable }: ToggleProps) {
  if (!connectable) {
    return <button className="sw locked" title="Coming soon" aria-label="Coming soon" />;
  }
  const on = conn !== null;
  return (
    <button
      className={`sw${on ? " on" : ""}`}
      aria-label={on ? "Disable sync" : "Enable sync"}
      onClick={on ? onDisable : onEnable}
    />
  );
}

// ── Row ───────────────────────────────────────────────────────────────────────

type RowProps = {
  provider: Provider;
  conn: Connection | null;
  searchTerm: string;
  statusFilter: string;
  onEnable: () => void;
  onDisable: (id: string) => void;
};

function ProviderRow({ provider: p, conn, searchTerm, statusFilter, onEnable, onDisable }: RowProps) {
  // client-side filter matching
  const nameMatch = !searchTerm || p.name.toLowerCase().includes(searchTerm.toLowerCase());
  const effectiveStatus = p.connectable
    ? (conn ? conn.status : "disconnected")
    : "soon";

  const statusMatch =
    statusFilter === "all" ||
    (statusFilter === "live" && effectiveStatus === "live") ||
    (statusFilter === "syncing" && effectiveStatus === "syncing") ||
    (statusFilter === "soon" && effectiveStatus === "soon");

  if (!nameMatch || !statusMatch) return null;

  const logo = p.logo
    ? <img src={`/logos/${p.logo}`} alt="" width={22} height={22} loading="lazy" />
    : null;

  const tile = p.tile
    ? <span className="tile" style={{ background: p.tile.bg }}>{p.tile.text}</span>
    : null;

  return (
    <tr data-name={p.name.toLowerCase()} data-status={effectiveStatus}>
      <td>
        <div className="src-cell">
          <span className="src-logo">{logo ?? tile}</span>
          <span className="src-name">
            <b>{p.name}</b>
            <span>{p.desc}</span>
          </span>
        </div>
      </td>
      <td className="c-status">
        {p.connectable
          ? <StatusCell conn={conn} />
          : <span className="st soon">Coming soon</span>}
      </td>
      <td className="c-items">
        {p.connectable && conn
          ? <span className="items-v">{conn.item_count.toLocaleString()}</span>
          : <span className="dash">—</span>}
      </td>
      <td className="c-sync">
        {p.connectable && conn
          ? <span className="sync-v">{relTime(conn.last_sync)}</span>
          : <span className="dash">—</span>}
      </td>
      <td className="c-enable">
        <Toggle
          connectable={!!p.connectable}
          conn={conn}
          onEnable={onEnable}
          onDisable={() => conn && onDisable(conn.connection_id)}
        />
      </td>
    </tr>
  );
}

// ── Category table ────────────────────────────────────────────────────────────

type CatTableProps = {
  category: Category;
  connByType: Record<string, Connection>;
  searchTerm: string;
  catFilter: string;
  statusFilter: string;
  onEnable: (provider: string) => void;
  onDisable: (id: string) => void;
};

function connOf(p: Provider, connByType: Record<string, Connection>): Connection | null {
  return p.connectable && p.connType ? (connByType[p.connType] ?? null) : null;
}

function CategoryTable({ category, connByType, searchTerm, catFilter, statusFilter, onEnable, onDisable }: CatTableProps) {
  // hide entire category when catFilter doesn't match
  if (catFilter !== "all" && catFilter !== category.label) return null;

  // count visible rows
  const visibleCount = category.providers.filter((p) => {
    const nameMatch = !searchTerm || p.name.toLowerCase().includes(searchTerm.toLowerCase());
    const conn = connOf(p, connByType);
    const effectiveStatus = p.connectable
      ? (conn ? conn.status : "disconnected")
      : "soon";
    const statusMatch =
      statusFilter === "all" ||
      (statusFilter === "live" && effectiveStatus === "live") ||
      (statusFilter === "syncing" && effectiveStatus === "syncing") ||
      (statusFilter === "soon" && effectiveStatus === "soon");
    return nameMatch && statusMatch;
  }).length;

  if (visibleCount === 0) return null;

  return (
    <section className="cat" data-cat={category.label}>
      <div className="cat-label">{category.label}</div>
      <div className="ds-card">
        <table>
          <thead>
            <tr>
              <th className="c-source">Source</th>
              <th className="c-status">Status</th>
              <th className="c-items">Items</th>
              <th className="c-sync">Last Sync</th>
              <th className="c-enable">Enable Sync</th>
            </tr>
          </thead>
          <tbody>
            {category.providers.map((p) => (
              <ProviderRow
                key={p.key}
                provider={p}
                conn={connOf(p, connByType)}
                searchTerm={searchTerm}
                statusFilter={statusFilter}
                onEnable={() => onEnable(p.connType ?? "")}
                onDisable={onDisable}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DataSources() {
  const [conns, setConns] = useState<Connection[]>([]);

  // toolbar state
  const [searchTerm, setSearchTerm] = useState("");
  const [catFilter, setCatFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [banner, setBanner] = useState<string | null>(null);

  const aliveRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    aliveRef.current = false;
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const refresh = useCallback(() => {
    getConnections().then(setConns).catch(() => {});
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  // Connections keyed by backend type (sharepoint / teams), first wins.
  const connByType: Record<string, Connection> = {};
  for (const c of conns) if (!connByType[c.type]) connByType[c.type] = c;

  // Poll connections for ~30s while a freshly-connected source crawls (syncing → live/error).
  const pollUntilSettled = useCallback((rounds = 10) => {
    let n = 0;
    const tick = async () => {
      if (!aliveRef.current) return;
      try {
        const cs = await getConnections();
        setConns(cs);
        if (!cs.some((c) => c.status === "syncing")) return;
      } catch { /* keep trying */ }
      if (++n < rounds && aliveRef.current) timerRef.current = setTimeout(tick, 3000);
    };
    tick();
  }, []);

  // Handle the return from Microsoft admin-consent.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const connected = p.get("connected");
    if (connected) { setBanner(`${connected[0].toUpperCase()}${connected.slice(1)} connected — syncing now…`); pollUntilSettled(); }
    else if (p.get("error") === "oauth") setBanner("Connection was cancelled or failed.");
    if (connected || p.get("error")) window.history.replaceState({}, "", "/admin/sources");
  }, [pollUntilSettled]);

  // Enabling a provider redirects to the Microsoft admin-consent screen.
  const onEnable = async (provider: string) => {
    if (!provider) return;
    try {
      const { auth_url } = await connectProvider(provider);
      window.location.href = auth_url;
    } catch { /* 403 → the layout gate re-prompts via admin-auth-error */ }
  };

  const onDisable = async (id: string) => {
    try {
      await disconnect(id);
      refresh();
    } catch { /* noop */ }
  };

  // Determine if any rows are visible across all categories
  const anyVisible = CATALOG.some((cat) => {
    if (catFilter !== "all" && catFilter !== cat.label) return false;
    return cat.providers.some((p) => {
      const nameMatch = !searchTerm || p.name.toLowerCase().includes(searchTerm.toLowerCase());
      const conn = connOf(p, connByType);
      const effectiveStatus = p.connectable
        ? (conn ? conn.status : "disconnected")
        : "soon";
      const statusMatch =
        statusFilter === "all" ||
        (statusFilter === "live" && effectiveStatus === "live") ||
        (statusFilter === "syncing" && effectiveStatus === "syncing") ||
        (statusFilter === "soon" && effectiveStatus === "soon");
      return nameMatch && statusMatch;
    });
  });

  return (
    <div className="ds-page">
      <div className="wrap">
        <div className="head">
          <h1>Data Sources</h1>
          <p>Connect sources to bring their content into the intelligence layer.</p>
        </div>

        {banner && <div className="admin-note" style={{ marginTop: 16 }}>{banner}</div>}

        {/* Toolbar */}
        <div className="toolbar">
          <label className="search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
            </svg>
            <input
              type="text"
              placeholder="Search sources…"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </label>
          <div className="filter">
            <select value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
              <option value="all">All categories</option>
              {CATALOG.map((c) => <option key={c.label} value={c.label}>{c.label}</option>)}
            </select>
          </div>
          <div className="filter">
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">All statuses</option>
              <option value="live">Live</option>
              <option value="syncing">Syncing</option>
              <option value="soon">Coming soon</option>
            </select>
          </div>
        </div>

        {/* Category tables */}
        <div id="cats">
          {CATALOG.map((cat) => (
            <CategoryTable
              key={cat.label}
              category={cat}
              connByType={connByType}
              searchTerm={searchTerm}
              catFilter={catFilter}
              statusFilter={statusFilter}
              onEnable={onEnable}
              onDisable={onDisable}
            />
          ))}
        </div>

        {!anyVisible && (
          <div className="ds-empty show">No sources match your search.</div>
        )}
      </div>
    </div>
  );
}
