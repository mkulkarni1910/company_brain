# Admin Maintenance Actions — Design

**Date:** 2026-06-03
**Status:** Approved (design)
**Surface:** SubStrateOS web Admin → Data Sources (`/admin/sources`) + brain-api `/admin`

## Problem

The admin has no in-product way to (a) manually re-sync a connected source or (b)
clear indexed content. During the pilot the `t-eval` index held only eval seed
documents (`up:*`, `local://` URLs), and the only way to clear them was out-of-band
index surgery. Admins need first-class **Sync** and **Purge** controls.

The side nav also carries a **Build → Developer** group that is an empty placeholder
stub (5 lines, no logic); it should be removed.

## Goals

- Per-source **Sync** button on each connected source (manual re-sync).
- **Purge Everything** button that clears tenant content (docs + ACL + activity),
  **keeping connections** so sources can be re-synced afterward.
- Remove the **Build/Developer** nav group and its stub page.
- Destructive purge is gated behind a typed confirmation and is tenant-scoped.
- Best-effort sub-steps report what actually happened — **no silent failures**.

## Non-goals (YAGNI)

- No "Sync All" button (per-source only, per product decision).
- No deletion of connections during purge.
- No index drop/recreate (delete by key only).
- No new RBAC — reuse the existing admin-key protection.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Button placement | On the Data Sources page (no new nav group) |
| Purge scope | Indexed docs + live ACL entries + activity events; keep connections |
| Sync behavior | Per-source only (a Sync button per connected source) |

## Design

### 1. Navigation — `web/app/admin/layout.tsx`
- Remove the entire `Build` group (and its `Developer` item) from `NAV`.
- Delete the stub route `web/app/admin/developer/page.tsx`.

### 2. Data Sources page — `web/app/admin/sources/page.tsx`
- **Per-source Sync:** for any provider row with an active connection
  (`conn !== null`), render a small **Sync** button beside the existing
  enable/disable `Toggle`. On click → `resync(conn.connection_id)` then reuse the
  existing `pollUntilSettled()` so the status animates syncing → live/error.
  The button is disabled while `conn.status === "syncing"`.
- **Purge Everything:** a danger-styled button in the page `head`. Opens a confirm
  modal that requires typing `PURGE` (exact match) to enable the confirm action.
  On confirm → `purgeEverything()`; show the returned summary in the page banner;
  then `refresh()`.

### 3. Admin API client — `web/lib/adminApi.ts`
- Add `export const purgeEverything = () => call<PurgeResult>("/admin/purge", { method: "POST" })`.
- `PurgeResult = { docs_deleted: number; acl_cleared: number | null; activity_cleared: number | null; errors: string[] }`.
- `resync(id)` already exists — wire it into the page.

### 4. Backend — `brain-api/app/api/admin.py` + `app/retrieval/ai_search_client.py`
- **`AISearchClient.delete_tenant_docs(*, tenant_id) -> int`** (new): page documents
  filtered by `tenant_id eq '<escaped>'` selecting the key (`chunk_id`), then
  `delete_documents([{ "chunk_id": k } for k in keys])` in batches; loop until a
  query returns no rows (delete is eventually consistent) with a safety cap on
  iterations. Returns total deleted.
- **`POST /admin/purge`** (new): same admin-key protection as the other `/admin`
  mutating routes. Tenant = `get_settings().brain_tenant_id`. Steps, each isolated:
  1. `docs_deleted = await ai_search.delete_tenant_docs(tenant_id=tenant)`
  2. ACL: best-effort delete of `acl:doc:{tenant}:*` keys via the ACL store
     (no-op when Redis host is unset, e.g. India). Returns count or `null`.
  3. Activity: best-effort ADX soft-delete `ActivityEvents | where TenantId == tid`.
     Degrades to `null` / an `errors[]` entry if ADX is unreachable or the managed
     identity lacks rights.
  Returns `{ docs_deleted, acl_cleared, activity_cleared, errors }`. A failure in a
  later step never rolls back earlier steps; the response makes partial outcomes
  explicit.

### 5. Error handling
- Purge sub-steps are wrapped independently; an exception appends to `errors[]`
  rather than failing the whole request (docs deletion is the primary, must-succeed
  step — if it raises, return 500).
- UI surfaces the summary + any `errors[]` in the existing `admin-note` banner.

### 6. Testing
- Backend: `delete_tenant_docs` batches+loops over a fake SearchClient and stops
  when empty; `/admin/purge` returns the summary and isolates a failing activity
  step into `errors[]` while still reporting `docs_deleted`. Tenant-scoping: the
  filter only targets the current tenant.
- Frontend: typecheck/build (existing CI). Manual: Sync animates a row; Purge
  requires typing `PURGE`; banner shows the result.

## Operational notes
- In the India deploy, docs are the only populated store: purge clears the search
  index (incl. seed data); ACL no-ops (no Redis); activity likely reports skipped
  until the ADX MI grant lands — the response says so.
- Deploy is the standard manual path (local amd64 build → ACR push → containerapp
  update) for brain-api, and the web app's own image/pipeline for the UI.
