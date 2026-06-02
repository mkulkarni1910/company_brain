# SharePoint Connect via Admin-Consent OAuth — Design Spec

**Date:** 2026-06-02
**Status:** Approved
**Builds on:** `2026-05-31-substrateos-admin-panel-design.md`

## Goal

Let an admin connect **their own organization's SharePoint** to the intelligence
layer by signing in and admin-consenting our app — the enterprise "connect your
workspace" model — then crawl all their SharePoint sites and ingest the content.

## Decisions (locked)

| Decision | Choice |
| --- | --- |
| OAuth model | **Admin-consent, app-only crawl.** Admin consents our multi-tenant app for their tenant; connector reads app-only via client-credentials. No per-user refresh tokens. |
| App tenancy | **Multi-tenant** (`AzureADMultipleOrgs`) so any org's admin can connect. |
| Site scope | **Crawl all sites** in the connected tenant (capped by `connector_max_items`). No picker. |
| ACL / isolation | Pilot model: ingested docs get `tenant=BRAIN_TENANT_ID` (t-eval) + `{tenant}:everyone`. Per-org isolation is a documented follow-up, NOT in scope. |
| Token store | None needed for app-only — store only `connected_tenant_id` on the Connection. Client secret already wired into brain-api (`AZURE_CLIENT_SECRET`). |

## Architecture

### Entra app (infra)
- App `19487212-e866-4726-a39e-cf55118dd4f3` ("SubStrateOS Connector"), already has
  Graph application perms `Sites.Read.All` + `Files.Read.All` consented in the home
  tenant. Changes:
  - `signInAudience` → `AzureADMultipleOrgs`.
  - Add web redirect URI `https://brain-api.redplant-161decbe.centralindia.azurecontainerapps.io/admin/connections/sharepoint/callback`.
- The connecting org's admin consent provisions the app's SP + role grants in *their* tenant.

### Backend (`brain-api`)
- **`app/connectors/oauth.py`** — pure helpers:
  - `admin_consent_url(*, client_id, redirect_uri, state) -> str` →
    `https://login.microsoftonline.com/organizations/v2.0/adminconsent?client_id=…&redirect_uri=…&state=…&scope=https://graph.microsoft.com/.default`.
  - Token endpoint is per-tenant: `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`.
- **OAuth state store** — small TTL'd CSRF store on the people graph (vertex label
  `cbrain_oauthstate`, key `state`, props `tenant_id`(brain tenant), `created_at`,
  JSON `data`). Methods `put(state)`, `consume(state) -> bool` (one-shot, expires
  > `oauth_state_ttl_seconds`, default 600). Lives in `CosmosConnectionStore`
  (or a sibling) — same degrade-on-error pattern.
- **`SharePointConnector`** — `__init__(tenant_id: str | None = None)`; `_token()`
  acquires a client-credentials token from the **connected tenant's** token endpoint
  using `azure_client_id` + `azure_client_secret`; if `tenant_id` is None it falls
  back to `DefaultAzureCredential` (home tenant, legacy). `list_sites`/`list_files`/
  `fetch_content` unchanged otherwise; still degrade to `[]`/`None`.
- **`Connection`** — add `connected_tenant_id: str | None = None`.
- **`SyncRunner`** — build the `SharePointConnector(tenant_id=connection.connected_tenant_id)`
  per run (so each connection crawls its own tenant). (Runner no longer takes a
  shared connector; the admin route passes a connector factory or the runner builds it.)
- **API routes** (`app/api/admin.py`, behind `require_admin_key` except the callback):
  - `POST /admin/connections/sharepoint/connect` → make `state`, store it, return
    `{auth_url}` (the admin-consent URL with our redirect_uri + state).
  - `GET /admin/connections/sharepoint/callback` → **no admin-key** (browser redirect
    from Microsoft); validate `admin_consent==True` + `state` (consume, one-shot);
    on success create `Connection{connection_id, tenant_id=BRAIN_TENANT, type=sharepoint,
    name="SharePoint — <tenant>", connected_tenant_id=<tenant>, status=syncing}`,
    persist, schedule `SyncRunner.run` (BackgroundTasks), then 302 to
    `{web_base}/admin/sources?connected=sharepoint`. On invalid/expired state →
    302 to `…/admin/sources?error=oauth`.
- **Config** — `web_base_url` (for the post-callback redirect; env `WEB_BASE_URL`),
  `oauth_state_ttl_seconds: int = 600`. `azure_client_id`/`azure_client_secret`
  already set in prod.

### Frontend (`web`)
- SharePoint **Enable Sync** toggle (off → on): call `connectSharePoint()` →
  `POST /admin/connections/sharepoint/connect` → `window.location = auth_url`.
  Remove the old site-picker modal path for SharePoint (connect = redirect now).
- On `/admin/sources` load, read `?connected=sharepoint` / `?error=oauth` query →
  show a toast/inline note + poll connections until the new one is `live`.
- Disable (on → off) unchanged: `disconnect(connection_id)`.
- `web/lib/adminApi.ts`: add `connectSharePoint(): {auth_url}`.

## Data flow (happy path)
1. Toggle on → `POST …/connect` → `{auth_url}` (state persisted).
2. Browser → Microsoft admin-consent → admin signs in + consents.
3. Microsoft → `GET …/callback?admin_consent=True&tenant=<T>&state=<S>`.
4. Callback validates state, creates Connection (syncing), schedules sync, 302 → web.
5. SyncRunner: client-credentials token for tenant `<T>` → enumerate sites/files →
   SourceDoc (`sp:<site>:<item>`, `tenant=t-eval`, `{t-eval}:everyone`) → IngestPipeline.
6. Connection → `live`, `item_count`, `last_sync`. Web row updates via poll.

## Error handling
- Invalid/expired/missing `state` → callback 302 to web with `?error=oauth` (no connection created).
- `admin_consent != True` (admin declined) → same error redirect.
- Graph token/enumeration failure for the connected tenant → `list_*` return `[]`;
  sync completes with 0 items, connection `error` if the token itself fails.
- All Cosmos ops degrade (no crash) as elsewhere.

## Testing
- `oauth.py`: `admin_consent_url` shape; per-tenant token URL.
- state store: put → consume (true once, false on replay), expiry → false.
- callback handler: valid state → connection created + bg scheduled; bad/expired state → error redirect, no connection.
- `SharePointConnector(tenant_id=…)`: token requested from the right tenant endpoint (mock httpx); degrade on error.
- `SyncRunner`: builds connector for `connected_tenant_id`, ingests, sets live.
- Manual: real admin-consent round-trip against the test M365 tenant → sites enumerate → files ingest → answerable in chat.

## Out of scope (v1)
Per-org brain-tenant isolation; site/library picker; incremental/delta sync; change
webhooks; per-file SharePoint ACL mapping; publisher verification.

## Known follow-ups
- Multi-org data isolation (per-tenant index / ACL) before real multi-customer use.
- Delta query (`/delta`) for incremental sync instead of full re-crawl.
- Token-less app-only relies on the client secret; rotate + Key Vault as for other secrets.
