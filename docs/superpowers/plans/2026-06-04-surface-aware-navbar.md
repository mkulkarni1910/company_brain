# Surface-Aware Navbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Teams/Slack from the web chat navbar, gate API/MCP chips on admin-configured surface state, and block the web UI when the admin disables the web surface.

**Architecture:** A new public `GET /surfaces` backend endpoint (user auth, no admin key) exposes `[{name, enabled}]`. The `Chat` component fetches this on mount, uses it to conditionally render API/MCP chips, and renders a blocked page if the web surface is disabled. Teams and Slack are removed entirely from frontend types and UI.

**Tech Stack:** FastAPI (Python), Next.js 14, React 18, TypeScript

---

## File Map

| File | Change |
|---|---|
| `substrateos-api/app/api/surfaces.py` | **Create** — public `GET /surfaces` route |
| `substrateos-api/app/main.py` | **Modify** — import & register surfaces router |
| `substrateos-api/tests/test_surfaces_api.py` | **Create** — endpoint tests |
| `web/lib/api.ts` | **Modify** — add `getSurfaces()` |
| `web/components/Chat.tsx` | **Modify** — remove Teams/Slack, gate API/MCP, blocked page |

---

## Task 1: Public surfaces backend endpoint

**Files:**
- Create: `substrateos-api/app/api/surfaces.py`
- Test: `substrateos-api/tests/test_surfaces_api.py`

- [ ] **Step 1: Write the failing test**

Create `substrateos-api/tests/test_surfaces_api.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.surfaces import router
from app.connectors.store import ConnectionStore
from tests.test_connector_store import FakeRedis


def _build_app(store: ConnectionStore) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.connection_store = store
    return app


@pytest.mark.asyncio
async def test_surfaces_returns_default_list():
    app = _build_app(ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/surfaces", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data]
    assert "web" in names
    assert "api" in names
    assert "mcp" in names


@pytest.mark.asyncio
async def test_surfaces_no_admin_key_required():
    """Regular users must be able to call this endpoint without an admin key."""
    app = _build_app(ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/surfaces", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_surfaces_enabled_field_present():
    app = _build_app(ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/surfaces", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    for s in r.json():
        assert "name" in s
        assert "enabled" in s
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
.venv/bin/pytest tests/test_surfaces_api.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — file doesn't exist yet.

- [ ] **Step 3: Create the surfaces route**

Create `substrateos-api/app/api/surfaces.py`:

```python
from fastapi import APIRouter, Depends, Request

from app.config import get_settings
from app.deps import get_connection_store
from app.connectors.store import ConnectionStore

router = APIRouter(tags=["surfaces"])


@router.get("/surfaces")
async def list_surfaces(
    store: ConnectionStore = Depends(get_connection_store),
) -> list[dict]:
    """Public read-only surface list — no admin key required.
    Returns [{name, enabled}] so the web app can gate surface chips.
    """
    tenant = get_settings().substrateos_tenant_id
    surfaces = await store.list_surfaces(tenant)
    return [{"name": s.name, "enabled": s.enabled} for s in surfaces]
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
.venv/bin/pytest tests/test_surfaces_api.py -v
```

Expected: all 3 tests PASS.

---

## Task 2: Register surfaces router in main.py

**Files:**
- Modify: `substrateos-api/app/main.py`

- [ ] **Step 1: Add import**

In `substrateos-api/app/main.py`, add alongside the other router imports (around line 19):

```python
from app.api.surfaces import router as surfaces_router
```

- [ ] **Step 2: Register the router**

In `substrateos-api/app/main.py`, add after the existing `app.include_router(conversations_router)` line (around line 199):

```python
app.include_router(surfaces_router)
```

- [ ] **Step 3: Smoke test the router is reachable**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
.venv/bin/pytest tests/test_surfaces_api.py tests/test_admin_api.py -v
```

Expected: all pass (admin tests confirm existing routes still work).

- [ ] **Step 4: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/substrateos-api
git add app/api/surfaces.py app/main.py tests/test_surfaces_api.py
git commit -m "feat: add public GET /surfaces endpoint (no admin key required)"
```

---

## Task 3: Add getSurfaces() to frontend API client

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Add the type and function**

In `web/lib/api.ts`, append after the `revokeToken` function (at the bottom of the file):

```typescript
export type SurfaceStatus = { name: string; enabled: boolean };

export async function getSurfaces(): Promise<SurfaceStatus[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/surfaces`);
    if (!resp.ok) return [];
    return (await resp.json()) as SurfaceStatus[];
  } catch {
    return [];
  }
}
```

The function fails open (returns `[]`) so the UI defaults to showing all chips when the fetch fails — it never hides chips due to a network error.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/web
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

---

## Task 4: Update Chat.tsx — remove Teams/Slack, gate API/MCP, blocked page

**Files:**
- Modify: `web/components/Chat.tsx`

This task has four logical sub-steps, committed together at the end.

- [ ] **Step 1: Update the Surface type (line 238)**

Find:
```typescript
type Surface = "Web" | "Teams" | "Slack" | "API" | "MCP";
```

Replace with:
```typescript
type Surface = "Web" | "API" | "MCP";
```

- [ ] **Step 2: Update SURFACE_META (lines 335–341)**

Find the entire `SURFACE_META` const:
```typescript
const SURFACE_META: Record<Surface, { icon: string; title: string; sub: string }> = {
  Web:   { icon: "🌐", title: "Use SubStrateOS on the web", sub: "You're using it right now" },
  API:   { icon: "🔌", title: "Use SubStrateOS via API", sub: "Grounded company context for your own apps & agents" },
  MCP:   { icon: "🧩", title: "Use SubStrateOS via MCP", sub: "Connect your AI assistant to SubStrateOS" },
  Slack: { icon: "💬", title: "Use SubStrateOS in Slack", sub: "Ask SubStrateOS without leaving your channels" },
  Teams: { icon: "💬", title: "Use SubStrateOS in Teams", sub: "Ask SubStrateOS without leaving your channels" },
};
```

Replace with:
```typescript
const SURFACE_META: Record<Surface, { icon: string; title: string; sub: string }> = {
  Web: { icon: "🌐", title: "Use SubStrateOS on the web", sub: "You're using it right now" },
  API: { icon: "🔌", title: "Use SubStrateOS via API", sub: "Grounded company context for your own apps & agents" },
  MCP: { icon: "🧩", title: "Use SubStrateOS via MCP", sub: "Connect your AI assistant to SubStrateOS" },
};
```

- [ ] **Step 3: Strip the "coming soon" branch from ConnectModal (lines 346, 409–418)**

Delete the `soon` variable declaration entirely — it will have no uses left after this step:
```typescript
  const soon = surface === "Slack" || surface === "Teams";
```
(just remove this line)

Then find and delete the "soon" modal footer block entirely:
```typescript
          {soon && (
            <div className="soon-wrap">
              <div className="big">{surface} app — coming soon</div>
              <div>You&apos;ll add the SubStrateOS {surface} app, then <code>/ask</code> SubStrateOS or @mention it in any channel. Answers stay scoped to each user&apos;s access.</div>
            </div>
          )}
```
Delete that block (replace with nothing — the `{surface === "API" && ...}` and `{surface === "MCP" && ...}` blocks already cover all remaining cases).

Also remove the `narrow` class logic since `soon` is now always false — find:
```typescript
      <div className={"modal" + (soon ? " narrow" : "")} onClick={(e) => e.stopPropagation()}>
```
Replace with:
```typescript
      <div className="modal" onClick={(e) => e.stopPropagation()}>
```

And remove the `{soon && <span className="pill-soon">soon</span>}` chip in the modal header:
```typescript
          {soon && <span className="pill-soon">soon</span>}
          <button className="m-x" onClick={onClose} style={soon ? { marginLeft: 12 } : undefined}>✕</button>
```
Replace with:
```typescript
          <button className="m-x" onClick={onClose}>✕</button>
```

- [ ] **Step 4: Add surfaces state to Chat component and fetch on mount**

Add the import at the top of Chat.tsx (with other lib/api imports):

```typescript
import { ..., getSurfaces, SurfaceStatus } from "@/lib/api";
```

Inside the `Chat` function (around line 422), add the surfaces state alongside existing state:

```typescript
  const [surfaceMap, setSurfaceMap] = useState<Record<string, boolean>>({});

  useEffect(() => {
    getSurfaces().then((list) => {
      const map: Record<string, boolean> = {};
      for (const s of list) map[s.name] = s.enabled;
      setSurfaceMap(map);
    });
  }, []);
```

The `{}` default means `surfaceMap["api"]` returns `undefined` (falsy) until the fetch completes — but we want fail-open. So define a helper right after:

```typescript
  // Fail-open: if surfaces haven't loaded yet (or fetch failed), treat as enabled.
  const surfaceEnabled = (name: string) => surfaceMap[name] !== false;
```

`!== false` means `undefined` (not yet loaded) → true (show chip), and `false` (explicitly disabled) → false (hide chip).

- [ ] **Step 5: Replace topbar chips (lines 506–511)**

Find:
```tsx
          <div className="surfaces">
            <button className="chip on" onClick={() => setConnectSurface(null)}><span className="d" />Web</button>
            {(["Teams", "Slack", "API", "MCP"] as Surface[]).map((s) => (
              <button key={s} className={"chip" + (connectSurface === s ? " sel" : "")} onClick={() => setConnectSurface(s)}>{s}</button>
            ))}
          </div>
```

Replace with:
```tsx
          <div className="surfaces">
            <button className="chip on" onClick={() => setConnectSurface(null)}><span className="d" />Web</button>
            {(["API", "MCP"] as Surface[]).filter((s) => surfaceEnabled(s.toLowerCase())).map((s) => (
              <button key={s} className={"chip" + (connectSurface === s ? " sel" : "")} onClick={() => setConnectSurface(s)}>{s}</button>
            ))}
          </div>
```

- [ ] **Step 6: Add web-disabled blocked page**

Just before the `return (` at the top of the Chat render (around line 452), add:

```tsx
  if (surfaceMap["web"] === false) {
    return (
      <div className="app app--norail" style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <div style={{ textAlign: "center", maxWidth: 400 }}>
          <div className="glyph big" style={{ margin: "0 auto 24px" }} />
          <h2 style={{ marginBottom: 12 }}>Web app disabled</h2>
          <p style={{ color: "var(--ink-faint)" }}>Your admin has disabled access to the SubStrateOS web interface. Contact your administrator to re-enable it.</p>
        </div>
      </div>
    );
  }
```

- [ ] **Step 7: Verify TypeScript compiles**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/web
npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain/web
git add lib/api.ts components/Chat.tsx
git commit -m "feat: surface-aware navbar — remove Teams/Slack, gate API/MCP on admin config, block if web disabled"
```

---

## Task 5: Deploy

- [ ] **Step 1: Deploy substrateos-api**

Use the `substrateos-deploy` skill or the manual pipeline:

```bash
# From repo root
# Build + push substrateos-api image, then roll out to Azure Container Apps (centralindia)
```

- [ ] **Step 2: Deploy substrateos-web**

```bash
# Build + push substrateos-web image, then roll out to Azure Container Apps (centralindia)
```

- [ ] **Step 3: Smoke test in prod**

Open `https://substrateos-web.redplant-161decbe.centralindia.azurecontainerapps.io`:
- Topbar shows only `Web` chip + any admin-enabled surface chips
- No Teams or Slack chips visible
- In the admin Surfaces page, toggle API off → refresh web app → API chip disappears
- Toggle API back on → chip reappears
