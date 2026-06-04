# Admin Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Overview and Surfaces admin pages to match the polished mockup design, with a working backend API for surface enable/disable management.

**Architecture:** Backend adds `SurfaceConfig` model + Redis-backed surface store methods + two REST endpoints (`GET/PATCH /admin/surfaces`). Frontend updates Overview to remove source_health and refresh attn/activity layout; Surfaces gets a full card-grid implementation with working toggles that call the new backend.

**Tech Stack:** FastAPI (Python), Pydantic, Redis (async), Next.js 14, TypeScript, React

---

### Task 1: Update Overview page — remove source_health, refresh layout

**Files:**
- Modify: `web/app/admin/page.tsx`
- Modify: `web/app/globals.css`
- Modify: `web/lib/adminApi.ts`
- Modify: `substrateos-api/app/api/admin.py`

- [ ] **Step 1: Update `.attn` CSS to card-style rows**

In `web/app/globals.css`, find the line:
```css
  .attn,.health,.activity{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px dashed var(--line-soft);font-size:13.5px}
  .attn:last-child,.health:last-child,.activity:last-child{border-bottom:none}
```

Replace with:
```css
  .attn{display:flex;align-items:center;gap:12px;padding:11px 14px;background:var(--paper-2);border:1px solid var(--line-soft);border-radius:10px;font-size:13.5px;margin-bottom:9px}
  .attn:last-child{margin-bottom:0}
  .health,.activity{display:flex;align-items:flex-start;gap:11px;padding:12px 0;border-bottom:1px solid var(--line-soft);font-size:13.5px}
  .health:last-child,.activity:last-child{border-bottom:none}
```

- [ ] **Step 2: Update `.where` to amber clickable link, add `.activity-body` styles**

In `web/app/globals.css`, find:
```css
  .where,.who-when,.status{margin-left:auto;font-family:var(--font-mono),monospace;font-size:10.5px;color:var(--ink-faint)}
```

Replace with:
```css
  .where{margin-left:auto;font-family:var(--font-mono),monospace;font-size:10.5px;color:var(--amber);font-weight:600;white-space:nowrap;cursor:pointer}
  .who-when{font-size:11px;color:var(--ink-faint);margin-top:2px}
  .status{margin-left:auto;font-family:var(--font-mono),monospace;font-size:10.5px;color:var(--ink-faint)}
  .activity-body{display:flex;flex-direction:column}
  .activity-text{font-size:13.5px;line-height:1.45}
  .activity-meta{font-size:11px;color:var(--ink-faint);margin-top:2px}
```

- [ ] **Step 3: Hide `.tile-hint` (subtexts removed per design)**

In `web/app/globals.css`, after the `.tile-hint` rule, add:
```css
  .tile-hint{display:none}
```

- [ ] **Step 4: Add optional `severity` field to `NeedsItem` type**

In `web/lib/adminApi.ts`, update:
```typescript
export type NeedsItem = { text: string; where: string; severity?: "error" | "warning" | "ok" };
```

- [ ] **Step 5: Add `severity` to backend needs_attention items**

In `substrateos-api/app/api/admin.py`, update the `needs` list inside `async def stats(...)`:
```python
    needs: list[dict] = []
    if not conns:
        needs.append({"text": "No data sources connected yet", "where": "Data Sources", "severity": "warning"})
    for c in conns:
        if c.status == "syncing":
            needs.append({"text": f"{c.name} is still indexing", "where": "Data Sources", "severity": "warning"})
        if c.status == "error":
            needs.append({"text": f"{c.name} sync failed: {c.error or 'unknown'}", "where": "Data Sources", "severity": "error"})
```

- [ ] **Step 6: Rewrite `web/app/admin/page.tsx`**

Replace the entire file:
```tsx
"use client";
import { useEffect, useState } from "react";
import { getStats, AdminStats } from "@/lib/adminApi";

const fmt = (n: number | null) => (n === null || n === undefined ? "—" : n.toLocaleString());

function relTime(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 5) return "now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function Overview() {
  const [s, setS] = useState<AdminStats | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { getStats().then(setS).catch(() => setErr(true)); }, []);
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
        {(s?.needs_attention ?? []).length === 0 && <p className="muted">All clear.</p>}
        {(s?.needs_attention ?? []).map((n, i) => (
          <div className="attn" key={i}>
            <span className={`dot ${n.severity === "error" ? "rose" : n.severity === "ok" ? "green" : "amber"}`} />
            <div>{n.text}</div>
            <span className="where">{n.where} ›</span>
          </div>
        ))}
      </section>
      <section className="card">
        <h3>Recent activity</h3>
        {(s?.recent_activity ?? []).length === 0 && <p className="muted">No activity yet.</p>}
        {(s?.recent_activity ?? []).map((a, i) => (
          <div className="activity" key={i}>
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
```

- [ ] **Step 7: Verify in browser**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/web && npm run dev
```

Open http://localhost:3000/admin. Verify: 4 stat tiles (no subtexts), Needs attention uses solid card rows with colored dots and amber "› " nav links, Recent activity shows text + actor/time meta below. Source health is gone.

- [ ] **Step 8: Commit**

```bash
git add web/app/admin/page.tsx web/app/globals.css web/lib/adminApi.ts substrateos-api/app/api/admin.py
git commit -m "feat: redesign admin overview — remove source health, update attn/activity layout"
```

---

### Task 2: Backend — SurfaceConfig model and store

**Files:**
- Modify: `substrateos-api/app/connectors/models.py`
- Modify: `substrateos-api/app/connectors/store.py`

- [ ] **Step 1: Add `SurfaceConfig` model**

In `substrateos-api/app/connectors/models.py`, append after the last class:
```python
class SurfaceConfig(BaseModel):
    name: str  # "slack" | "teams" | "web" | "api" | "mcp"
    enabled: bool = True
    installed: bool = False
    workspace_name: str | None = None
```

- [ ] **Step 2: Add surface key + defaults to `store.py`**

In `substrateos-api/app/connectors/store.py`:

Update the import to include `SurfaceConfig`:
```python
from app.connectors.models import ActivityEntry, Connection, SurfaceConfig, SyncJob
```

Add after the existing `_activity_key` function:
```python
def _surfaces_key(tenant: str) -> str: return f"surfaces:{tenant}"

_DEFAULT_SURFACES: list[SurfaceConfig] = [
    SurfaceConfig(name="slack"),
    SurfaceConfig(name="teams"),
    SurfaceConfig(name="web"),
    SurfaceConfig(name="api"),
    SurfaceConfig(name="mcp"),
]
```

- [ ] **Step 3: Add `list_surfaces` and `put_surface` methods to `ConnectionStore`**

Add these two methods inside the `ConnectionStore` class (after `recent_activity`):
```python
    async def list_surfaces(self, tenant: str) -> list[SurfaceConfig]:
        if self._r is None:
            return list(_DEFAULT_SURFACES)
        try:
            raw = await self._r.hgetall(_surfaces_key(tenant))
            stored = {k: SurfaceConfig.model_validate_json(v) for k, v in raw.items()}
            return [stored.get(s.name, s) for s in _DEFAULT_SURFACES]
        except _ERRORS as e:
            logger.warning("list_surfaces failed: %s", e)
            return list(_DEFAULT_SURFACES)

    async def put_surface(self, tenant: str, surface: SurfaceConfig) -> None:
        if self._r is None:
            return
        try:
            await self._r.hset(_surfaces_key(tenant), surface.name, surface.model_dump_json())
        except _ERRORS as e:
            logger.warning("put_surface failed: %s", e)
```

- [ ] **Step 4: Run existing tests**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all tests pass (changes are additive only).

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/connectors/models.py substrateos-api/app/connectors/store.py
git commit -m "feat: add SurfaceConfig model and Redis-backed surface store methods"
```

---

### Task 3: Backend — Surface API endpoints

**Files:**
- Modify: `substrateos-api/app/api/admin.py`

- [ ] **Step 1: Add `SurfacePatch` request model**

In `substrateos-api/app/api/admin.py`, add after the existing `PurgeResult` class definition:
```python
class SurfacePatch(BaseModel):
    enabled: bool
```

- [ ] **Step 2: Add `GET /admin/surfaces`**

Add before the `@router.post("/purge")` route:
```python
@router.get("/surfaces")
async def list_surfaces(
    store: ConnectionStore = Depends(get_connection_store),
) -> list[dict]:
    tenant = get_settings().substrateos_tenant_id
    surfaces = await store.list_surfaces(tenant)
    return [s.model_dump() for s in surfaces]
```

- [ ] **Step 3: Add `PATCH /admin/surfaces/{name}`**

Immediately after the `list_surfaces` route:
```python
_VALID_SURFACES = {"slack", "teams", "web", "api", "mcp"}

@router.patch("/surfaces/{name}")
async def patch_surface(
    name: str,
    body: SurfacePatch,
    store: ConnectionStore = Depends(get_connection_store),
) -> dict:
    if name not in _VALID_SURFACES:
        raise HTTPException(status_code=400, detail=f"unknown surface: {name!r}")
    tenant = get_settings().substrateos_tenant_id
    surfaces = await store.list_surfaces(tenant)
    surface = next((s for s in surfaces if s.name == name), None)
    if surface is None:
        from app.connectors.models import SurfaceConfig
        surface = SurfaceConfig(name=name)
    surface.enabled = body.enabled
    await store.put_surface(tenant, surface)
    return surface.model_dump()
```

- [ ] **Step 4: Smoke-test the endpoints**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
uvicorn app.main:app --reload --port 8000
```

In a second terminal (replace `your-key` with the value of `ADMIN_API_KEY` in `.env`):
```bash
# List all surfaces
curl -s -H "x-admin-key: your-key" http://localhost:8000/admin/surfaces | python3 -m json.tool
# Expected: array of 5 objects, all enabled:true

# Disable MCP
curl -s -X PATCH -H "x-admin-key: your-key" -H "Content-Type: application/json" \
  -d '{"enabled": false}' http://localhost:8000/admin/surfaces/mcp | python3 -m json.tool
# Expected: {"name":"mcp","enabled":false,"installed":false,"workspace_name":null}

# Verify persisted
curl -s -H "x-admin-key: your-key" http://localhost:8000/admin/surfaces | python3 -m json.tool
# Expected: mcp.enabled is false, others still true
```

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/admin.py
git commit -m "feat: add GET/PATCH /admin/surfaces endpoints"
```

---

### Task 4: Frontend — Surface API types and client

**Files:**
- Modify: `web/lib/adminApi.ts`

- [ ] **Step 1: Add `SurfaceConfig` type**

In `web/lib/adminApi.ts`, add after the `PurgeResult` type block:
```typescript
export type SurfaceConfig = {
  name: string;
  enabled: boolean;
  installed: boolean;
  workspace_name: string | null;
};
```

- [ ] **Step 2: Add `getSurfaces` and `patchSurface` calls**

At the bottom of `web/lib/adminApi.ts`, add:
```typescript
export const getSurfaces = () => call<SurfaceConfig[]>("/admin/surfaces");
export const patchSurface = (name: string, enabled: boolean) =>
  call<SurfaceConfig>(`/admin/surfaces/${name}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/web
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/lib/adminApi.ts
git commit -m "feat: add SurfaceConfig type and getSurfaces/patchSurface API calls"
```

---

### Task 5: Frontend — CSS and Surfaces page

**Files:**
- Modify: `web/app/globals.css`
- Modify: `web/app/admin/surfaces/page.tsx`

- [ ] **Step 1: Add surface CSS to `globals.css`**

In `web/app/globals.css`, at the end of the `.admin` scoped block (before the closing `@media` rule), add:
```css
  /* surfaces */
  .surf-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .surf-card{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:22px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:14px;transition:opacity .2s}
  .surf-card.surf-off{opacity:.72}
  .surf-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
  .surf-head{display:flex;align-items:center;gap:12px}
  .surf-logo{width:40px;height:40px;border-radius:11px;flex:none;display:grid;place-items:center;box-shadow:0 2px 8px rgba(0,0,0,.12)}
  .surf-logo svg{width:20px;height:20px;color:#fff}
  .sl-slack{background:#4A154B}
  .sl-teams{background:#5059C9}
  .sl-web{background:radial-gradient(circle at 30% 25%,#ffcf7e,var(--amber) 60%,#9a5e0e)}
  .sl-api{background:var(--teal)}
  .sl-mcp{background:#1e1b2e}
  .surf-name{font-family:var(--font-fraunces),serif;font-size:16px;font-weight:600;line-height:1.2}
  .surf-chip{font-family:var(--font-mono),monospace;font-size:9px;letter-spacing:.6px;text-transform:uppercase;color:var(--ink-faint);border:1px solid var(--line-soft);border-radius:99px;padding:2px 8px;margin-top:3px;display:inline-block}
  .surf-desc{font-size:13px;color:var(--ink-faint);line-height:1.56;flex:1}
  .surf-blocked{display:none;align-items:center;gap:8px;padding:9px 12px;background:#fdf0f2;border:1px solid #f5c6cf;border-radius:9px;font-size:12.5px;color:var(--rose);font-weight:500}
  .surf-blocked.show{display:flex}
  .surf-blocked svg{width:14px;height:14px;flex:none}
  .surf-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:auto}
  .surf-scope{font-family:var(--font-mono),monospace;font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--ink-faint)}
  .surf-install-btn{font-size:12.5px;font-weight:600;padding:7px 14px;border-radius:9px;border:none;cursor:pointer;transition:.15s;white-space:nowrap}
  .surf-install-btn:disabled{opacity:.5;cursor:not-allowed}
  .btn-slack{background:#4A154B;color:#fff}.btn-slack:hover:not(:disabled){background:#3d1140}
  .btn-teams{background:#5059C9;color:#fff}.btn-teams:hover:not(:disabled){background:#404db0}
  .surf-installed{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:500;color:var(--green)}
  .surf-url{font-family:var(--font-mono),monospace;font-size:11px;color:var(--ink-faint);background:var(--paper-2);border:1px solid var(--line-soft);border-radius:7px;padding:5px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
  @media(max-width:960px){.surf-grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:640px){.surf-grid{grid-template-columns:1fr}}
```

- [ ] **Step 2: Implement `web/app/admin/surfaces/page.tsx`**

Replace the entire file:
```tsx
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
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/web
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Test in browser with backend running**

```bash
# Terminal 1 — backend
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd /Users/lokesh/Desktop/RFpilot/company_brain/web
npm run dev
```

Open http://localhost:3000/admin/surfaces. Verify:
- 5 cards render in 3-col grid (Slack, Teams, Web on row 1; API, MCP on row 2)
- Web/API/MCP show endpoint URL chip in the footer
- Slack and Teams show "Install to X" button; click → "Installing…" → "Installed in Your workspace"
- Toggling any surface off: card fades to 72% opacity, rose blocked banner appears, install button disabled
- Toggle back on: banner hides, card brightens, install button re-enables
- State persists on page refresh (backed by Redis)

- [ ] **Step 5: Commit**

```bash
git add web/app/globals.css web/app/admin/surfaces/page.tsx
git commit -m "feat: implement Surfaces admin page with live enable/disable and install flow"
```
