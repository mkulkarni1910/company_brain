"use client";
import { useEffect, useState } from "react";
import { getSurfaces, patchSurface, SurfaceConfig } from "@/lib/adminApi";

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

type CardProps = {
  meta: SurfaceMeta;
  config: SurfaceConfig;
  onToggle: (enabled: boolean) => void;
  onInstall: () => void;
  installing: boolean;
};

function SurfaceCard({ meta, config, onToggle, onInstall, installing }: CardProps) {
  const { enabled, installed, workspace_name } = config;
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
        {meta.installable ? (
          installed ? (
            <div className="surf-installed">
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", display: "inline-block", flexShrink: 0 }} />
              Installed in {workspace_name ?? "your workspace"}
            </div>
          ) : (
            <button
              className={`surf-install-btn btn-${meta.name}`}
              onClick={onInstall}
              disabled={!enabled || installing}
            >
              {installing ? "Installing…" : `Install to ${meta.label}`}
            </button>
          )
        ) : (
          meta.endpoint ? <span className="surf-url">{meta.endpoint}</span> : <span />
        )}
        <span className="surf-scope">{meta.scope}</span>
      </div>
    </div>
  );
}

export default function Surfaces() {
  const [configs, setConfigs] = useState<SurfaceConfig[]>([]);
  const [installing, setInstalling] = useState<string | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getSurfaces().then(setConfigs).catch(() => setErr(true));
  }, []);

  const configOf = (name: string): SurfaceConfig =>
    configs.find((c) => c.name === name) ?? { name, enabled: true, installed: false, workspace_name: null };

  const handleToggle = async (name: string, enabled: boolean) => {
    setConfigs((prev) => prev.map((c) => c.name === name ? { ...c, enabled } : c));
    try {
      const updated = await patchSurface(name, enabled);
      setConfigs((prev) => prev.map((c) => c.name === name ? updated : c));
    } catch {
      setConfigs((prev) => prev.map((c) => c.name === name ? { ...c, enabled: !enabled } : c));
    }
  };

  const handleInstall = async (name: string) => {
    setInstalling(name);
    try {
      await patchSurface(name, true);
      setConfigs((prev) => prev.map((c) =>
        c.name === name ? { ...c, enabled: true, installed: true, workspace_name: "Your workspace" } : c
      ));
    } finally {
      setInstalling(null);
    }
  };

  return (
    <div className="admin-page">
      <header className="admin-head">
        <h1>Surfaces</h1>
        <p>Where SubStrateOS shows up — enable surfaces and install integrations for your team.</p>
      </header>
      {err && <div className="admin-note">Couldn&apos;t load surface config. Check the admin key / API.</div>}
      <div className="surf-grid">
        {SURFACES.map((meta) => (
          <SurfaceCard
            key={meta.name}
            meta={meta}
            config={configOf(meta.name)}
            onToggle={(enabled) => handleToggle(meta.name, enabled)}
            onInstall={() => handleInstall(meta.name)}
            installing={installing === meta.name}
          />
        ))}
      </div>
    </div>
  );
}
