# SharePoint Admin-Consent OAuth Connector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let an admin connect their org's SharePoint by admin-consenting our multi-tenant app, then crawl that tenant app-only and ingest all sites.

**Architecture:** Web toggle → `POST /admin/connections/sharepoint/connect` returns the Microsoft admin-consent URL (with a TTL'd CSRF `state` stored in Cosmos) → browser consents → Microsoft redirects to `GET …/callback` → validate state, create a `Connection{connected_tenant_id}`, background-sync via client-credentials against that tenant → 302 back to web.

**Tech Stack:** FastAPI · httpx · MS Graph (client-credentials per tenant) · Cosmos Gremlin · Next.js.

**Spec:** `docs/superpowers/specs/2026-06-02-sharepoint-admin-consent-oauth-design.md`

---

## Task 1: Config

**Files:** Modify `brain-api/app/config.py` (after `connector_max_items`).

- [ ] Add:
```python
    # SharePoint admin-consent OAuth
    web_base_url: str = "http://localhost:3000"        # for the post-callback redirect (env WEB_BASE_URL)
    oauth_state_ttl_seconds: int = 600
    brain_api_base_url: str = "http://localhost:8000"  # our public base, for the OAuth redirect_uri (env BRAIN_API_BASE_URL)
```
- [ ] Verify: `cd brain-api && uv run python -c "from app.config import get_settings; print(get_settings().oauth_state_ttl_seconds)"` → `600`.
- [ ] Commit: `git add brain-api/app/config.py && git commit -m "feat(oauth): config (web/api base urls, state ttl)"`

---

## Task 2: OAuth helpers

**Files:** Create `brain-api/app/connectors/oauth.py`; Test `brain-api/tests/test_connector_oauth.py`.

- [ ] **Test:**
```python
from app.connectors.oauth import admin_consent_url, token_url

def test_admin_consent_url():
    u = admin_consent_url(client_id="cid", redirect_uri="https://x/cb", state="s1")
    assert u.startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent?")
    assert "client_id=cid" in u and "state=s1" in u
    assert "redirect_uri=https%3A%2F%2Fx%2Fcb" in u
    assert "scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in u

def test_token_url():
    assert token_url("tenant-123") == "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token"
```
- [ ] Run → fail. `cd brain-api && uv run pytest tests/test_connector_oauth.py -v`
- [ ] **Implement `oauth.py`:**
```python
from __future__ import annotations
from urllib.parse import urlencode

_GRAPH_DEFAULT = "https://graph.microsoft.com/.default"

def admin_consent_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    q = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": _GRAPH_DEFAULT,
    })
    return f"https://login.microsoftonline.com/organizations/v2.0/adminconsent?{q}"

def token_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
```
- [ ] Run → pass. Commit: `git add brain-api/app/connectors/oauth.py brain-api/tests/test_connector_oauth.py && git commit -m "feat(oauth): admin-consent url + per-tenant token url helpers"`

---

## Task 3: OAuth state store (CSRF)

**Files:** Modify `brain-api/app/connectors/cosmos_store.py` and `brain-api/app/connectors/store.py`; Test `brain-api/tests/test_oauth_state.py`.

Add `put_oauth_state(state, tenant)` + `consume_oauth_state(state) -> str | None` (returns the brain tenant if valid + unexpired, one-shot) to BOTH stores.

- [ ] **Cosmos** (`cosmos_store.py`): add label `_OAUTH = "cbrain_oauthstate"` and:
```python
    async def put_oauth_state(self, state: str, tenant: str) -> None:
        import time
        await self._upsert(_OAUTH, "st", state, tenant, json.dumps({"tenant": tenant, "ts": int(time.time())}))

    async def consume_oauth_state(self, state: str) -> str | None:
        import time
        from app.config import get_settings
        rows = await self._values(_OAUTH, None, None)  # all states are tenant-agnostic lookups by key below
        # direct lookup by key:
        rows = await self._values(_OAUTH, tenant=None, keyprop="st", keyval=state) if False else rows
        try:
            got = await self._g.submit(
                f"g.V().has('{_OAUTH}','st', k).values('data')", {"k": state})
        except Exception:  # noqa: BLE001
            return None
        if not got:
            return None
        try:
            d = json.loads(got[0])
        except Exception:  # noqa: BLE001
            return None
        # one-shot: drop it
        try:
            await self._g.submit(f"g.V().has('{_OAUTH}','st', k).drop()", {"k": state})
        except Exception:  # noqa: BLE001
            pass
        if int(time.time()) - int(d.get("ts", 0)) > get_settings().oauth_state_ttl_seconds:
            return None
        return d.get("tenant")
```
NOTE TO IMPLEMENTER: the `_values(...)` lines above are noise — delete them; the real impl is the direct `g.submit` lookup by `st`. `_upsert` already sets `tenant_id`=tenant (partition) which is fine; the lookup is by `st` only (state is globally unique). Keep `_upsert`/`_values` partition behaviour; for state, querying by `st` across partitions is acceptable (low volume, short-lived).

- [ ] **Redis** (`store.py`): add (guard `self._r is None`):
```python
    async def put_oauth_state(self, state: str, tenant: str) -> None:
        if self._r is None:
            return
        with contextlib.suppress(*_ERRORS):
            await self._r.set(f"oauth:state:{state}", tenant, ex=get_settings().oauth_state_ttl_seconds)

    async def consume_oauth_state(self, state: str) -> str | None:
        if self._r is None:
            return None
        try:
            return await self._r.getdel(f"oauth:state:{state}")
        except _ERRORS:
            return None
```
(import `get_settings` at top of store.py if not present.)

- [ ] **Test** (`test_oauth_state.py`) — Cosmos via FakeGraph (reuse from test_connector_cosmos_store), Redis via FakeRedis:
```python
import pytest
from app.connectors.cosmos_store import CosmosConnectionStore
from tests.test_connector_cosmos_store import FakeGraph

@pytest.mark.asyncio
async def test_state_one_shot():
    st = CosmosConnectionStore(graph=FakeGraph())
    await st.put_oauth_state("s1", "t-eval")
    assert await st.consume_oauth_state("s1") == "t-eval"
    assert await st.consume_oauth_state("s1") is None   # one-shot
    assert await st.consume_oauth_state("nope") is None
```
NOTE: FakeGraph must handle the `.drop()` by key `st`; its existing label/key parsing covers `has('cbrain_oauthstate','st', k)` and `.drop()`. Verify the regex `has\('([^']+)'` picks the label and bindings `k` the key. If FakeGraph's `.values('data')` branch requires `tid`, relax it to return by key alone when `tid` is None (adjust FakeGraph in the test file).
- [ ] Run → pass. Commit: `git add -A && git commit -m "feat(oauth): TTL'd one-shot CSRF state store (cosmos + redis)"`

---

## Task 4: Connection.connected_tenant_id

**Files:** Modify `brain-api/app/connectors/models.py`; Test: extend `tests/test_connector_models.py`.

- [ ] Add to `Connection`: `connected_tenant_id: str | None = None`.
- [ ] Test: `Connection(..., connected_tenant_id="t123").connected_tenant_id == "t123"` and default None.
- [ ] Run → pass. Commit.

---

## Task 5: Tenant-parameterized SharePointConnector

**Files:** Modify `brain-api/app/connectors/sharepoint.py`; Test: extend `tests/test_connector_sharepoint.py`.

- [ ] Change `__init__` + `_token`:
```python
    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        # Connected-tenant app-only via client credentials; else home-tenant MI/env (legacy).
        from app.connectors.oauth import token_url
        s = get_settings()
        if self._tenant_id and s.azure_client_id and s.azure_client_secret:
            async with httpx.AsyncClient(timeout=15.0) as http:
                r = await http.post(token_url(self._tenant_id), data={
                    "client_id": s.azure_client_id,
                    "client_secret": s.azure_client_secret,
                    "scope": _SCOPE,
                    "grant_type": "client_credentials",
                })
                r.raise_for_status()
                return r.json()["access_token"]
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token(_SCOPE)
            return tok.token
        finally:
            await cred.close()
```
(`_SCOPE` is already `https://graph.microsoft.com/.default`.)
- [ ] **Test:** monkeypatch httpx to assert the token endpoint is the connected tenant's, and that a token is returned; and that errors still degrade (`list_sites` → []). Example:
```python
@pytest.mark.asyncio
async def test_token_uses_connected_tenant(monkeypatch):
    import app.connectors.sharepoint as sp
    from app.config import get_settings
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid"); monkeypatch.setenv("AZURE_CLIENT_SECRET", "sec")
    get_settings.cache_clear()
    captured = {}
    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "tok"}
    class FakeClient:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def post(self, url, data=None): captured["url"]=url; return FakeResp()
    monkeypatch.setattr(sp.httpx, "AsyncClient", FakeClient)
    c = sp.SharePointConnector(tenant_id="tenantX")
    tok = await c._token()
    assert tok == "tok" and captured["url"].endswith("/tenantX/oauth2/v2.0/token")
    get_settings.cache_clear()
```
- [ ] Run → pass. Commit.

---

## Task 6: Connect + callback API routes

**Files:** Modify `brain-api/app/api/admin.py`; Test: extend `tests/test_admin_api.py`.

- [ ] Imports: `import secrets`, `from fastapi import Request` (already), `from fastapi.responses import RedirectResponse`, `from app.connectors.oauth import admin_consent_url`, `from app.connectors.sharepoint import SharePointConnector`.

- [ ] **Connect (POST, admin-key gated):**
```python
@router.post("/connections/sharepoint/connect")
async def sharepoint_connect(
    store: ConnectionStore = Depends(get_connection_store),
) -> dict:
    s = get_settings()
    state = secrets.token_urlsafe(24)
    await store.put_oauth_state(state, s.brain_tenant_id)
    redirect_uri = f"{s.brain_api_base_url}/admin/connections/sharepoint/callback"
    return {"auth_url": admin_consent_url(client_id=s.azure_client_id or "", redirect_uri=redirect_uri, state=state)}
```

- [ ] **Callback (GET, NO admin-key — define on a separate router without the dependency, or add an exemption).** Because the router has a router-level `require_admin_key`, create a second router for the callback:
```python
# at module top, after `router = APIRouter(... dependencies=[Depends(require_admin_key)])`
callback_router = APIRouter(prefix="/admin", tags=["admin"])  # NO admin-key (browser redirect)

@callback_router.get("/connections/sharepoint/callback")
async def sharepoint_callback(
    request: Request,
    state: str = "",
    tenant: str = "",
    admin_consent: str = "",
    error: str = "",
    store: ConnectionStore = Depends(get_connection_store),
    pipeline=Depends(get_ingest_pipeline),
) -> RedirectResponse:
    s = get_settings()
    web = s.web_base_url.rstrip("/")
    brain_tenant = await store.consume_oauth_state(state) if state else None
    ok = (admin_consent.lower() == "true") and bool(tenant) and (brain_tenant is not None) and not error
    if not ok:
        return RedirectResponse(url=f"{web}/admin/sources?error=oauth", status_code=302)
    conn = Connection(
        connection_id=uuid.uuid4().hex, tenant_id=brain_tenant, type="sharepoint",
        site_id=tenant, name=f"SharePoint — {tenant[:8]}", web_url="",
        connected_tenant_id=tenant, status="syncing",
    )
    await store.put_connection(conn)
    runner = SyncRunner(connector=SharePointConnector(tenant_id=tenant), pipeline=pipeline, store=store)
    request.app.state  # (BackgroundTasks not available on a redirect-returning GET cleanly; schedule via asyncio)
    import asyncio
    asyncio.create_task(runner.run(connection=conn, actor="admin"))
    return RedirectResponse(url=f"{web}/admin/sources?connected=sharepoint", status_code=302)
```
NOTE TO IMPLEMENTER: use `asyncio.create_task` (not BackgroundTasks) since we return a RedirectResponse; the task runs detached on the running loop. Delete the stray `request.app.state` line. Keep `request` param only if needed (it isn't — drop `request` to avoid an unused arg, and drop the unused import).

- [ ] **Register `callback_router`** in `app/main.py`: `app.include_router(callback_router)` (import it alongside `admin_router`). In `admin.py` export both.

- [ ] **Tests:** 
  - `POST /admin/connections/sharepoint/connect` with key → 200, body has `auth_url` containing `adminconsent` + a state; without key → 403.
  - callback with bad state → 302 to `…/admin/sources?error=oauth`, no connection created.
  - callback with valid state (pre-put a state via the store) + `admin_consent=true&tenant=T` → 302 to `…?connected=sharepoint` and a connection with `connected_tenant_id=T` exists.
  (Use the FakeRedis-backed `ConnectionStore` + env ADMIN_API_KEY/BRAIN_TENANT_ID as in existing test_admin_api.py. For the callback's `asyncio.create_task`, inject a fake pipeline/connector via app.state or accept that the task will fail harmlessly in the test — assert only the redirect + persisted connection. To avoid a real SharePointConnector network call in the test, monkeypatch `SyncRunner.run` to a no-op coroutine.)
- [ ] Run → pass. Commit.

---

## Task 7: Frontend — connect via redirect

**Files:** Modify `web/lib/adminApi.ts`, `web/app/admin/sources/page.tsx`.

- [ ] `adminApi.ts`: add
```typescript
export const connectSharePoint = () =>
  call<{ auth_url: string }>("/admin/connections/sharepoint/connect", { method: "POST" });
```
- [ ] `sources/page.tsx`: for the SharePoint row, the **Enable** handler becomes:
```tsx
const onEnableSharePoint = async () => {
  try { const { auth_url } = await connectSharePoint(); window.location.href = auth_url; }
  catch { /* AdminAuthError already re-prompts via the gate */ }
};
```
Wire the SharePoint provider's toggle `onEnable` to `onEnableSharePoint` (replace the old `openPicker`). Remove the site-picker modal usage for SharePoint (can delete the modal + getSites import if nothing else uses them).
- [ ] On mount, read the return query and surface a note + poll:
```tsx
useEffect(() => {
  const p = new URLSearchParams(window.location.search);
  if (p.get("connected") === "sharepoint") { setBanner("Connecting SharePoint… syncing now."); refresh(); }
  if (p.get("error") === "oauth") setBanner("SharePoint connection was cancelled or failed.");
  if (p.get("connected") || p.get("error")) window.history.replaceState({}, "", "/admin/sources");
}, []);
```
(Add a `banner` state + render it as an `.admin-note`. After `connected`, poll `getConnections()` every 3s for ~30s until a `sharepoint` connection is `live`.)
- [ ] Verify: `cd web && pnpm typecheck && pnpm build`. Commit.

---

## Task 8: Infra — multi-tenant app + redirect URI + env

- [ ] Make the connector app multi-tenant + add the redirect URI:
```bash
az ad app update --id 19487212-e866-4726-a39e-cf55118dd4f3 --sign-in-audience AzureADMultipleOrgs
az ad app update --id 19487212-e866-4726-a39e-cf55118dd4f3 \
  --web-redirect-uris "https://brain-api.redplant-161decbe.centralindia.azurecontainerapps.io/admin/connections/sharepoint/callback"
```
- [ ] Set brain-api env (new revision):
```bash
az containerapp update -n brain-api -g rg-company-brain-india --set-env-vars \
  "WEB_BASE_URL=https://substrateos-web.redplant-161decbe.centralindia.azurecontainerapps.io" \
  "BRAIN_API_BASE_URL=https://brain-api.redplant-161decbe.centralindia.azurecontainerapps.io"
```
- [ ] (Also add to `web/.env.production` nothing new — web only calls our API.)

---

## Task 9: Build, deploy, verify

- [ ] Backend: `cd brain-api && uv run pytest tests/test_connector_oauth.py tests/test_oauth_state.py tests/test_connector_sharepoint.py tests/test_admin_api.py tests/test_connector_models.py -q` → pass; `uv run ruff check app/`.
- [ ] Frontend: `cd web && pnpm typecheck && pnpm build`.
- [ ] Build+push amd64 `brain-api:india6` + `substrateos-web:india6` (buildx, cbbuilder) → roll both apps.
- [ ] Verify: `GET /admin/connections/sharepoint/connect` (with key) returns an `auth_url`; hitting the callback with no state → 302 `?error=oauth`.
- [ ] **Manual end-to-end (user):** open `/admin/sources`, toggle SharePoint → consent on the real M365 tenant → returns to sources → connection goes `live` with items > 0 → ask a question in chat grounded on a SharePoint doc.
- [ ] Update memory.

---

## Self-Review Notes
- **Spec coverage:** connect/callback (T6), multi-tenant app + redirect (T8), state store (T3), tenant-parameterized connector (T5), Connection field (T4), web redirect+return (T7), config (T1), helpers (T2), deploy/verify (T9). Covered.
- **Implementer traps flagged inline:** (a) callback lives on a separate `callback_router` WITHOUT `require_admin_key`; (b) use `asyncio.create_task` for the detached sync (not BackgroundTasks) since we return a RedirectResponse; delete the stray `request.app.state`/unused `request`; (c) in Task 3 delete the noise `_values(...)` lines — the real state lookup is the direct `g.submit` by `st`; relax FakeGraph if needed.
- **Type consistency:** `connected_tenant_id` used in models (T4), connector (T5), callback (T6). `put_oauth_state`/`consume_oauth_state` consistent across stores (T3) + callback/connect (T6). `connectSharePoint` (T7).
