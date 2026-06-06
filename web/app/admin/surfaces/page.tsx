"use client";
import { useEffect, useState } from "react";
import {
  getBotStatus, getSurfaces, patchSurface, downloadTeamsManifest,
  getGithubConfig, putGithubConfig,
  BotStatus, SurfaceConfig, GithubConfig,
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
    desc: "SubstrateOS app in Slack — answers questions in any channel or DM, responds to @-mentions. Each reply is scoped to what that user can see.",
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
    name: "github", label: "GitHub", tag: "Tool", logoClass: "sl-github",
    desc: "Action connector — where SubstrateOS acts. Users raise AI-drafted pull requests to your configured repo from chat. Each PR is authored by the requesting user via their own GitHub login.",
    scope: "All employees", installable: true,
    blockedMsg: "GitHub tool disabled — raise-PR requests are refused.",
  },
  {
    name: "web", label: "Web", tag: "All", logoClass: "sl-web",
    desc: "First-party chat and search interface at your SubstrateOS URL. Disabling blocks the web app entirely for all users.",
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

type SurfFilter = "all" | "enabled" | "disabled" | "installed" | "needs-setup" | "builtin";
type SetupState = "installed" | "needs-setup" | "builtin";

const FILTERS: { key: SurfFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "enabled", label: "Enabled" },
  { key: "disabled", label: "Disabled" },
  { key: "installed", label: "Installed" },
  { key: "needs-setup", label: "Needs setup" },
  { key: "builtin", label: "Built-in" },
];

// A surface that can't be installed (web/api/mcp) is always-on "built-in";
// installable ones (slack/teams) are either connected or still need setup.
function setupOf(meta: SurfaceMeta, config: SurfaceConfig): SetupState {
  if (!meta.installable) return "builtin";
  return config.installed ? "installed" : "needs-setup";
}

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
  github: (
    <svg viewBox="0 0 24 24" fill="currentColor" width={20} height={20}>
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
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

function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
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
        <div className="setup-hd">
          <div className="lg sl-teams">{ICONS.teams}</div>
          <div>
            <h3>Connect SubstrateOS to Microsoft Teams</h3>
            <div className="sub">One-time setup · ~8 minutes · Azure Bot + Teams Admin Center access</div>
          </div>
        </div>
        <div className="setup-note">
          <InfoIcon />
          Answers render as Adaptive Cards, and meeting context appears in the side panel during calls.
        </div>
        <ol className="setup-steps">
          <li className="setup-step"><span className="num">1</span><div className="t">In the <b>Azure Portal</b>, create an <b>Azure Bot</b> resource and set its messaging endpoint to:<code className="setup-code">{API_URL}/bot/teams</code></div></li>
          <li className="setup-step"><span className="num">2</span><div className="t">Copy the <b>App ID</b> and <b>App Password</b>, add them to your server environment, and restart the API.<code className="setup-code">TEAMS_BOT_APP_ID=…   TEAMS_BOT_APP_PASSWORD=…</code></div></li>
          <li className="setup-step"><span className="num">3</span><div className="t">Download the manifest package below, then upload it in <b>Teams Admin Center → Apps → Manage apps → Upload an app</b>.</div></li>
          <li className="setup-step"><span className="num">4</span><div className="t">Done — this card will show <b>Active</b> on the next load.</div></li>
        </ol>
        {dlErr && <p className="setup-err">Download failed — check that TEAMS_BOT_APP_ID is set and the API is running.</p>}
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
        <div className="setup-hd">
          <div className="lg sl-slack">{ICONS.slack}</div>
          <div>
            <h3>Connect SubstrateOS to Slack</h3>
            <div className="sub">One-time setup · ~5 minutes · admin access to your Slack workspace</div>
          </div>
        </div>
        <div className="setup-note">
          <InfoIcon />
          Each reply is scoped to what the asking user can see — SubstrateOS uses their identity, not a shared bot account.
        </div>
        <ol className="setup-steps">
          <li className="setup-step"><span className="num">1</span><div className="t">Go to <b>api.slack.com/apps</b> → <b>Create New App</b> → <b>From scratch</b>, and name it <b>SubstrateOS</b>.</div></li>
          <li className="setup-step"><span className="num">2</span><div className="t">Under <b>OAuth &amp; Permissions</b>, add the bot token scopes:<code className="setup-code">app_mentions:read   chat:write   im:read   im:write</code></div></li>
          <li className="setup-step"><span className="num">3</span><div className="t">Under <b>Event Subscriptions</b>, enable events and set the Request URL, then subscribe to <code>app_mention</code> and <code>message.im</code>.<code className="setup-code">{API_URL}/bot/slack</code></div></li>
          <li className="setup-step"><span className="num">4</span><div className="t"><b>Install to Workspace</b>, then copy the <b>Bot User OAuth Token</b> and the <b>Signing Secret</b>.</div></li>
          <li className="setup-step"><span className="num">5</span><div className="t">Add them to your server environment and restart the API — this card will flip to <b>Active</b>.<code className="setup-code">SLACK_BOT_TOKEN=xoxb-…   SLACK_SIGNING_SECRET=…</code></div></li>
        </ol>
        <div className="modal-foot">
          <button className="modal-close" onClick={onClose}>Close</button>
          <a className="surf-install-btn btn-slack" href="https://api.slack.com/apps" target="_blank" rel="noreferrer" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Open Slack API ↗</a>
        </div>
      </div>
    </div>
  );
}

function GithubInstallModal({ onClose, onSaved }: { onClose: () => void; onSaved: (cfg: GithubConfig) => void }) {
  const [ownerRepo, setOwnerRepo] = useState("");
  const [base, setBase] = useState("main");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getGithubConfig().then((c) => {
      if (c.owner && c.repo) { setOwnerRepo(`${c.owner}/${c.repo}`); setSaved(true); }
      setBase(c.base_branch || "main");
    }).catch(() => {});
  }, []);

  const save = async () => {
    const [owner, repo] = ownerRepo.split("/").map((s) => s.trim());
    if (!owner || !repo) { setErr("Enter the repo as owner/repo."); return; }
    setSaving(true); setErr(null);
    try {
      const cfg = await putGithubConfig(owner, repo, base.trim() || "main");
      onSaved(cfg);
      setSaved(true);
    } catch {
      setErr("Save failed — check the admin key / API.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="admin-modal" onClick={onClose}>
      <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="setup-hd">
          <div className="lg sl-github">{ICONS.github}</div>
          <div>
            <h3>Connect SubstrateOS to GitHub</h3>
            <div className="sub">One-time setup · ~5 minutes · a GitHub account that can create OAuth Apps</div>
          </div>
        </div>
        <div className="setup-note">
          <InfoIcon />
          One app credential for SubstrateOS (this setup) — then each user connects their own GitHub from chat, so every PR is authored by the person who asked.
        </div>
        <ol className="setup-steps">
          <li className="setup-step">
            <span className="num">1</span>
            <div className="t">
              On GitHub go to <b>Settings → Developer settings → OAuth Apps → New OAuth App</b>. Set the callback URL to:
              <code className="setup-code">{API_URL}/auth/github/callback</code>
            </div>
          </li>
          <li className="setup-step">
            <span className="num">2</span>
            <div className="t">
              Copy the <b>Client ID</b> and generate a <b>Client Secret</b>, add them to the server environment, and restart the API.
              <code className="setup-code">GITHUB_CLIENT_ID=…   GITHUB_CLIENT_SECRET=…</code>
            </div>
          </li>
          <li className="setup-step">
            <span className="num">3</span>
            <div className="t">
              Enter the repository SubstrateOS raises PRs against.
              <div className="setup-repo">
                <input
                  className="repo-owner"
                  type="text"
                  placeholder="owner/repo"
                  aria-label="owner/repo"
                  value={ownerRepo}
                  onChange={(e) => setOwnerRepo(e.target.value)}
                />
                <input
                  className="repo-branch"
                  type="text"
                  placeholder="base branch"
                  aria-label="base branch"
                  value={base}
                  onChange={(e) => setBase(e.target.value)}
                />
                <button className="setup-save" onClick={save} disabled={saving}>
                  {saving ? "Saving…" : saved ? "Saved ✓" : "Save repo"}
                </button>
              </div>
              {err && <div className="setup-repo-err">{err}</div>}
              {saved && !err && ownerRepo && (
                <div style={{ fontSize: "12px", color: "var(--green)", marginTop: "6px" }}>
                  Saved — {ownerRepo} · branch: {base || "main"}
                </div>
              )}
            </div>
          </li>
          <li className="setup-step">
            <span className="num">4</span>
            <div className="t">
              Done — the card shows <b>Connected to owner/repo</b>. Users connect their own GitHub the first time they ask for a PR.
            </div>
          </li>
        </ol>
        <div className="modal-foot">
          <button className="modal-close" onClick={onClose}>Close</button>
          <a
            className="surf-install-btn btn-github"
            href="https://github.com/settings/developers"
            target="_blank"
            rel="noreferrer"
            style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}
          >
            Open GitHub OAuth Apps ↗
          </a>
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
      <>
        <div className="surf-installed">
          <span className="d" />
          Installed in {workspace_name ?? "your workspace"}
        </div>
        <button className="surf-setup" onClick={onInstall}>Setup guide</button>
      </>
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
        {meta.name === "github" ? "Connect GitHub" : `Install to ${meta.label}`}
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
      </div>
    </div>
  );
}

export default function Surfaces() {
  const [configs, setConfigs] = useState<SurfaceConfig[]>([]);
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [ghCfg, setGhCfg] = useState<GithubConfig | null>(null);
  const [installModal, setInstallModal] = useState<"teams" | "slack" | "github" | null>(null);
  const [err, setErr] = useState(false);
  const [filter, setFilter] = useState<SurfFilter>("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    Promise.all([getSurfaces(), getBotStatus(), getGithubConfig()])
      .then(([surfaces, status, cfg]) => {
        setConfigs(surfaces);
        setBotStatus(status);
        setGhCfg(cfg);
        // Auto-heal: if bot is configured but not yet marked installed, sync DB.
        const heal = (name: string, wsName: string, configured: boolean) => {
          const sc = surfaces.find((s) => s.name === name);
          if (configured && sc && !sc.installed) {
            patchSurface(name, sc.enabled, { installed: true, workspace_name: wsName })
              .then((updated) =>
                setConfigs((prev) => prev.map((c) => (c.name === name ? updated : c)))
              )
              .catch(() => {});
          }
        };
        heal("teams", "Microsoft Teams", status.teams.configured);
        heal("slack", "Slack", status.slack.configured);
        if (cfg.app_configured && cfg.repo_configured) {
          heal("github", `${cfg.owner}/${cfg.repo}`, true);
        }
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
    if (name === "teams" || name === "slack" || name === "github") setInstallModal(name);
  };

  const enriched = SURFACES.map((meta) => {
    const config = configOf(meta.name);
    return {
      meta,
      config,
      setup: setupOf(meta, config),
      status: (config.enabled ? "enabled" : "disabled") as SurfFilter,
      search: `${meta.label} ${meta.tag} ${meta.scope} ${meta.desc}`.toLowerCase(),
    };
  });

  const counts = enriched.reduce<Record<string, number>>((acc, s) => {
    acc.all = (acc.all ?? 0) + 1;
    acc[s.status] = (acc[s.status] ?? 0) + 1;
    acc[s.setup] = (acc[s.setup] ?? 0) + 1;
    return acc;
  }, {});

  const q = query.trim().toLowerCase();
  const shown = enriched.filter((s) => {
    const matchesFilter =
      filter === "all" ? true
      : filter === "enabled" || filter === "disabled" ? s.status === filter
      : s.setup === filter;
    return matchesFilter && (!q || s.search.includes(q));
  });

  return (
    <div className="admin-page">
    <div className="admin-wrap">
      <header className="admin-head">
        <h1>Surfaces</h1>
        <p>Where SubstrateOS shows up — enable surfaces and install integrations for your team.</p>
      </header>
      {err && <div className="admin-note">Couldn&apos;t load surface config. Check the admin key / API.</div>}

      <div className="runs-toolbar">
        <div className="search run-search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input
            placeholder="Search surfaces…"
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

      <div className="surf-grid">
        {shown.map(({ meta, config }) => {
          const bc =
            meta.name === "teams" ? (botStatus?.teams.configured ?? false) :
            meta.name === "slack" ? (botStatus?.slack.configured ?? false) :
            meta.name === "github" ? (botStatus?.github?.configured ?? false) : false;
          return (
            <SurfaceCard
              key={meta.name}
              meta={meta}
              config={config}
              onToggle={(enabled) => handleToggle(meta.name, enabled)}
              onInstall={() => handleInstall(meta.name)}
              botConfigured={bc}
            />
          );
        })}
        {shown.length === 0 && <div className="runs-empty" style={{ gridColumn: "1/-1" }}>No surfaces match this filter.</div>}
      </div>
      {installModal === "teams" && <TeamsInstallModal onClose={() => setInstallModal(null)} />}
      {installModal === "slack" && <SlackInstallModal onClose={() => setInstallModal(null)} />}
      {installModal === "github" && <GithubInstallModal onClose={() => setInstallModal(null)} onSaved={(c) => setGhCfg(c)} />}
    </div>
    </div>
  );
}
