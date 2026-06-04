"use client";
import { useEffect, useState } from "react";
import {
  getBotStatus, getSurfaces, patchSurface, downloadTeamsManifest,
  BotStatus, SurfaceConfig,
} from "@/lib/adminApi";

type SurfaceMeta = {
  name: string;
  label: string;
  desc: string;
  tag: string;
  logoClass: string;
  scope: string;
  installable: boolean;
  blockedMsg: string;
  endpoint?: string;
};

const SURFACES: SurfaceMeta[] = [
  {
    name: "slack", label: "Slack", tag: "Individual", logoClass: "sl-slack",
    desc: "SubStrateOS app in Slack — answers questions in any channel or DM, responds to @-mentions. Each reply is scoped to what that user can see.",
    scope: "All employees", installable: true,
    blockedMsg: "Slack surface disabled — all Slack access is blocked.",
  },
  {
    name: "teams", label: "Teams", tag: "Team", logoClass: "sl-teams",
    desc: "Personal and channel bot in Microsoft Teams. Answers render as Adaptive Cards; meeting context appears in the side panel during calls.",
    scope: "All employees", installable: true,
    blockedMsg: "Teams surface disabled — all Teams access is blocked.",
  },
  {
    name: "web", label: "Web", tag: "All", logoClass: "sl-web",
    desc: "First-party chat and search interface at your SubStrateOS URL. Disabling blocks the web app entirely for all users.",
    scope: "All employees", installable: false,
    blockedMsg: "Web app disabled — users will see a blocked page.",
    endpoint: "app.substrateos.ai",
  },
  {
    name: "api", label: "API", tag: "Platform", logoClass: "sl-api",
    desc: "REST endpoint for apps to query the context layer programmatically. Disabling rejects all API calls, including any integrations built on top.",
    scope: "Developers", installable: false,
    blockedMsg: "API disabled — all programmatic access is rejected.",
    endpoint: "api.substrateos.ai",
  },
  {
    name: "mcp", label: "MCP", tag: "Platform", logoClass: "sl-mcp",
    desc: "MCP server for Copilot Studio, Azure AI Foundry, or any MCP client. Disabling blocks all MCP connections workspace-wide.",
    scope: "Developers", installable: false,
    blockedMsg: "MCP disabled — all MCP server connections are blocked.",
    endpoint: "mcp.substrateos.ai",
  },
];

const ICONS: Record<string, React.ReactNode> = {
  slack: (
    <svg viewBox="0 0 24 24" fill="currentColor" width={20} height={20}>
      <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"/>
    </svg>
  ),
  teams: (
    <svg viewBox="0 0 24 24" fill="currentColor" width={20} height={20}>
      <path d="M19.5 8.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm1.5 1h-3a1.5 1.5 0 0 0-1.5 1.5V16h1.5v-5h3v5H23v-5a1.5 1.5 0 0 0-1.5-1.5zM13 9H9a2 2 0 0 0-2 2v7h2v-3h4v3h2v-7a2 2 0 0 0-2-2zm0 4H9v-2h4v2z"/>
      <circle cx="11" cy="4.5" r="2.5"/>
    </svg>
  ),
  web: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" width={20} height={20}>
      <circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/>
    </svg>
  ),
  api: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" width={20} height={20}>
      <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
    </svg>
  ),
  mcp: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" width={20} height={20}>
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
    </svg>
  ),
};

function BlockedIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
    </svg>
  );
}

const API_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function TeamsInstallModal({ onClose }: { onClose: () => void }) {
  const [downloading, setDownloading] = useState(false);
  const [dlErr, setDlErr] = useState(false);

  const handleDownload = async () => {
    setDownloading(true); setDlErr(false);
    try { await downloadTeamsManifest(); }
    catch { setDlErr(true); }
    finally { setDownloading(false); }
  };

  return (
    <div className="admin-modal" onClick={onClose}>
      <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>Install SubStrateOS in Microsoft Teams</h3>
        <ol style={{ paddingLeft: 18, margin: "0 0 16px", lineHeight: 1.7, fontSize: 13 }}>
          <li>In <b>Azure Portal</b>, create an <b>Azure Bot</b> resource. Set the messaging endpoint to:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              {API_URL}/bot/teams
            </code>
          </li>
          <li>Copy the <b>App ID</b> and <b>App Password</b>, then set in your server environment:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              TEAMS_BOT_APP_ID=… TEAMS_BOT_APP_PASSWORD=…
            </code>
            &nbsp;and restart the API.
          </li>
          <li>Download the manifest package and upload it in <b>Teams Admin Center → Apps → Manage apps → Upload an app</b>.</li>
          <li>Done — this card will show Active on next load.</li>
        </ol>
        {dlErr && <p style={{ color: "var(--rose)", fontSize: 12, margin: "0 0 8px" }}>Download failed — check that TEAMS_BOT_APP_ID is set and the API is running.</p>}
        <div className="modal-foot">
          <button className="modal-close" onClick={onClose}>Close</button>
          <button className="surf-install-btn btn-teams" onClick={handleDownload} disabled={downloading}>
            {downloading ? "Preparing…" : "Download manifest.zip"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SlackInstallModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="admin-modal" onClick={onClose}>
      <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>Install SubStrateOS in Slack</h3>
        <ol style={{ paddingLeft: 18, margin: "0 0 16px", lineHeight: 1.7, fontSize: 13 }}>
          <li>Go to <b>api.slack.com/apps</b> → <b>Create new app</b> → From scratch → name it <b>SubStrateOS</b>.</li>
          <li>Under <b>OAuth &amp; Permissions</b>, add bot scopes: <code>app_mentions:read</code>, <code>chat:write</code>, <code>im:read</code>, <code>im:write</code>.</li>
          <li>Under <b>Event Subscriptions</b> → enable → set Request URL to:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              {API_URL}/bot/slack
            </code>
            <br />Subscribe to <code>app_mention</code> and <code>message.im</code>.
          </li>
          <li><b>Install to workspace</b>, copy the <b>Bot User OAuth Token</b> and <b>Signing Secret</b>.</li>
          <li>Set in your server environment:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              SLACK_BOT_TOKEN=xoxb-… SLACK_SIGNING_SECRET=…
            </code>
            &nbsp;and restart the API — the card will show Active.
          </li>
        </ol>
        <div className="modal-foot">
          <button className="modal-close" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

type CardProps = {
  meta: SurfaceMeta;
  config: SurfaceConfig;
  onToggle: (enabled: boolean) => void;
  onInstall: () => void;
  botConfigured: boolean;
};

function SurfaceCard({ meta, config, onToggle, onInstall, botConfigured }: CardProps) {
  const { enabled, installed, workspace_name } = config;

  const footer = meta.installable ? (
    installed ? (
      <div className="surf-installed">
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", display: "inline-block", flexShrink: 0 }} />
        Installed in {workspace_name ?? "your workspace"}
        {/* keep setup reachable: manifest re-download / install steps */}
        <button
          onClick={onInstall}
          style={{ background: "none", border: "none", padding: 0, marginLeft: 8, color: "var(--ink-faint)", fontSize: 12, textDecoration: "underline", cursor: "pointer" }}
        >
          {meta.name === "teams" ? "manifest / setup" : "setup"}
        </button>
      </div>
    ) : botConfigured && meta.name === "teams" ? (
      <button
        className="surf-install-btn btn-teams"
        onClick={onInstall}
        disabled={!enabled}
      >
        Download manifest.zip
      </button>
    ) : (
      <button
        className={`surf-install-btn btn-${meta.name}`}
        onClick={onInstall}
        disabled={!enabled}
      >
        Install to {meta.label}
      </button>
    )
  ) : (
    meta.endpoint ? <span className="surf-url">{meta.endpoint}</span> : <span />
  );

  return (
    <div className={`surf-card${enabled ? "" : " surf-off"}`}>
      <div className="surf-top">
        <div className="surf-head">
          <div className={`surf-logo ${meta.logoClass}`}>{ICONS[meta.name]}</div>
          <div>
            <div className="surf-name">{meta.label}</div>
            <span className="surf-chip">{meta.tag}</span>
          </div>
        </div>
        <button
          className={`sw${enabled ? " on" : ""}`}
          aria-label={enabled ? `Disable ${meta.label}` : `Enable ${meta.label}`}
          onClick={() => onToggle(!enabled)}
        />
      </div>
      <div className="surf-desc">{meta.desc}</div>
      <div className={`surf-blocked${enabled ? "" : " show"}`}>
        <BlockedIcon />
        {meta.blockedMsg}
      </div>
      <div className="surf-foot">
        {footer}
        <span className="surf-scope">{meta.scope}</span>
      </div>
    </div>
  );
}

export default function Surfaces() {
  const [configs, setConfigs] = useState<SurfaceConfig[]>([]);
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [installModal, setInstallModal] = useState<"teams" | "slack" | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    Promise.all([getSurfaces(), getBotStatus()])
      .then(([surfaces, status]) => {
        setConfigs(surfaces);
        setBotStatus(status);
        // Auto-heal: if bot is configured but not yet marked installed, sync DB.
        const heal = (name: string, wsName: string, configured: boolean) => {
          const cfg = surfaces.find((s) => s.name === name);
          if (configured && cfg && !cfg.installed) {
            patchSurface(name, cfg.enabled, { installed: true, workspace_name: wsName })
              .then((updated) =>
                setConfigs((prev) => prev.map((c) => (c.name === name ? updated : c)))
              )
              .catch(() => {});
          }
        };
        heal("teams", "Microsoft Teams", status.teams.configured);
        heal("slack", "Slack", status.slack.configured);
      })
      .catch(() => setErr(true));
  }, []);

  const configOf = (name: string): SurfaceConfig =>
    configs.find((c) => c.name === name) ?? { name, enabled: true, installed: false, workspace_name: null };

  const handleToggle = async (name: string, enabled: boolean) => {
    setConfigs((prev) => prev.map((c) => (c.name === name ? { ...c, enabled } : c)));
    try {
      const updated = await patchSurface(name, enabled);
      setConfigs((prev) => prev.map((c) => (c.name === name ? updated : c)));
    } catch {
      setConfigs((prev) => prev.map((c) => (c.name === name ? { ...c, enabled: !enabled } : c)));
    }
  };

  const handleInstall = (name: string) => {
    if (name === "teams" || name === "slack") setInstallModal(name);
  };

  return (
    <div className="admin-page">
    <div className="admin-wrap">
      <header className="admin-head">
        <h1>Surfaces</h1>
        <p>Where SubStrateOS shows up — enable surfaces and install integrations for your team.</p>
      </header>
      {err && <div className="admin-note">Couldn&apos;t load surface config. Check the admin key / API.</div>}
      <div className="surf-grid">
        {SURFACES.map((meta) => {
          const bc =
            meta.name === "teams" ? (botStatus?.teams.configured ?? false) :
            meta.name === "slack" ? (botStatus?.slack.configured ?? false) : false;
          return (
            <SurfaceCard
              key={meta.name}
              meta={meta}
              config={configOf(meta.name)}
              onToggle={(enabled) => handleToggle(meta.name, enabled)}
              onInstall={() => handleInstall(meta.name)}
              botConfigured={bc}
            />
          );
        })}
      </div>
      {installModal === "teams" && <TeamsInstallModal onClose={() => setInstallModal(null)} />}
      {installModal === "slack" && <SlackInstallModal onClose={() => setInstallModal(null)} />}
    </div>
    </div>
  );
}
