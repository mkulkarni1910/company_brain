# SubStrateOS Admin Panel — Design Spec

**Date:** 2026-05-31
**Status:** Approved
**Author:** Lokesh (with Claude)

## Goal

Give admins a dedicated panel — separate URL, same app — to **connect SharePoint
files into the intelligence layer** (the existing ingest → AI Search pipeline), plus
an at-a-glance Overview dashboard. Visual design mirrors the reference "Overview"
mockup, restyled into the existing SubStrateOS design system.

## Decisions (locked)

| Decision | Choice |
| --- | --- |
| Placement | New `/admin` route group inside the existing `web/` Next app. Separate URL, same deployment, same Easy Auth gate, same design tokens. |
| Connector depth | Real MS Graph wiring with graceful degradation. Returns empty/"permission pending" until `Sites.Read.All`/`Files.Read.All` is consented — flips live with no code change. |
| v1 dashboard scope | **Overview** + **Data Sources** functional. Surfaces / Permissions / Developer are nav stubs. |
| Admin auth | Reuse existing `x-admin-key` (`ADMIN_API_KEY`). Sent on all `/admin/*` calls, on top of Easy Auth. |
| Connect unit / trigger | Connect a SharePoint **site** → **auto-ingest everything** (all supported files) via a background job. |
| Overview numbers | Real where cheap (items indexed, sources live, queries·7d, active users), honest `—` where no data. No fabricated numbers. |

## Architecture

### Frontend (`web/`)

Route group `web/app/admin/`:
- `layout.tsx` — admin shell: left nav rail (WORKSPACE → Overview; CONNECT → Data
  Sources, Surfaces, Permissions; BUILD → Developer), `ADMIN` badge, admin-key gate.
- `page.tsx` — **Overview**: 4 stat tiles, Needs Attention, Source Health, Recent Activity.
- `sources/page.tsx` — **Data Sources**: connection list + Connect-SharePoint flow + live sync progress.
- `surfaces/`, `permissions/`, `developer/` — stub pages ("coming soon" panels).
- `web/lib/adminApi.ts` — admin API client (sends `x-admin-key`, reuses `authedFetch` Easy-Auth wrapper).
- Admin styles appended to `web/app/globals.css` (single-stylesheet convention).

**Admin-key gate:** prompts once, stores key in `sessionStorage`, includes as
`x-admin-key` header. A `403` clears the stored key and re-prompts.
**Known limitation (documented):** any Easy-Auth'd user who knows the key gets in.
Real Entra admin-role RBAC is deferred.

### Backend (`brain-api`)

New `app/connectors/` module:
- `sharepoint.py` — `SharePointConnector`:
  - `list_sites()` → Graph `GET /sites?search=*`. Returns `[{site_id, name, web_url}]`.
  - `list_files(site_id)` → enumerate document-library `driveItems` recursively;
    filter to extractable types; cap at `connector_max_items` (default 500), log truncation.
  - `fetch_content(drive_id, item_id, mime)` → Graph `/content` → extracted text.
  - Token via `DefaultAzureCredential` `.default` (same path as `MSGraphSearchFetcher`).
  - **Every Graph call degrades to `[]`/`None` on error** (401s today, never raises).
- `extract.py` — text extraction. `.txt/.md/.html/.csv` inline (no deps); `.docx` via
  `python-docx`; `.pdf` via `pypdf`. Unsupported (pptx/xlsx/images) → skipped + counted.
- `store.py` (Redis) — `ConnectionStore`:
  - Connections per tenant: `connections:{tenant}` → records `{connection_id, type,
    site_id, name, web_url, status: live|syncing|error, last_sync, item_count, error}`.
  - Sync jobs: `connector:job:{tenant}:{job_id}` → `{status: queued|running|succeeded|
    failed, total, processed, skipped, errors, started, finished}`.
  - Admin activity log: `admin:activity:{tenant}` (list, cap 50).
  - All reads degrade to empty on Redis error.
- `sync.py` — `SyncRunner.run(connection)`: enumerate files → map each to `SourceDoc`
  → `IngestPipeline.process()`; update job progress; record activity. Runs via FastAPI
  `BackgroundTasks` (no new queue infra in v1).

**File → SourceDoc mapping:** `doc_id=sp:{site_id}:{item_id}`, `source="sharepoint"`,
real `source_url/title/created_at/modified_at`, `author_id` best-effort from Graph,
`acl_principals=["{tenant}:everyone"]` (v1 — matches current corpus + `PILOT_SINGLE_TENANT`;
real per-file ACL deferred), `mime` from Graph.

**Stats** (`MetricsStore`, Redis, fire-and-forget on `/query`):
- `metrics:queries:{tenant}:{yyyymmdd}` — `INCR` → **Queries·7d** = sum of last 7 days.
- `metrics:users:{tenant}:{yyyymmdd}` — `PFADD` user id → **Active users** = `PFCOUNT` union of last 7.
- **Items indexed** = `AISearchClient.count_docs(tenant)` (new; tenant-filtered count).
- **Sources live** = count of connections with `status=live`.
- Source Health / Recent Activity / Needs Attention derive from connections + activity log.
- No data → `—` / "no data yet". Never fabricated.

### New admin API (all behind `require_admin_key`)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/admin/stats` | tiles + source health + recent activity + needs attention |
| GET | `/admin/connections` | list connections + status |
| GET | `/admin/sharepoint/sites` | available SP sites (`[]` on 401) |
| POST | `/admin/connections` | connect a site → kick off auto-sync, returns `job_id` |
| POST | `/admin/connections/{id}/sync` | re-sync |
| DELETE | `/admin/connections/{id}` | remove connection record (indexed docs left in place) |
| GET | `/admin/jobs/{job_id}` | sync job status for polling |

### New config (`app/config.py`)

`connector_max_items: int = 500` (env `CONNECTOR_MAX_ITEMS`).

### New dependencies (`brain-api`)

`python-docx`, `pypdf` (pure-Python, light).

## Data flow (connect SharePoint)

1. Admin opens Data Sources → clicks "Connect SharePoint".
2. UI `GET /admin/sharepoint/sites` → list (or empty "permission pending" state).
3. Admin picks a site → `POST /admin/connections {site_id}`.
4. Backend creates connection (`status=syncing`), starts `SyncRunner` in BackgroundTasks, returns `job_id`.
5. Runner enumerates files → for each supported file: fetch content → extract text →
   `SourceDoc` → `IngestPipeline.process()` (chunk → embed → AI Search upsert). Updates job counters.
6. On finish: connection `status=live`, `item_count` set, activity logged.
7. UI polls `GET /admin/jobs/{job_id}` → live progress bar; on done, refreshes connection list + stats.

## Error handling / degradation (matches codebase conventions)

- Graph unauthorized/unreachable → `list_sites`/`list_files` return `[]`; UI shows
  "Connect blocked — Graph permission pending (Sites.Read.All)".
- Redis down → connection/job/metrics reads return empty; UI shows `—`.
- Unsupported file types → skipped + counted in job summary (visible, not silent).
- Extraction failure on a single file → that file skipped + counted; sync continues.
- Item cap hit → truncation logged + surfaced in job summary.
- BackgroundTasks sync interrupted by container restart → job left `running`; re-sync
  is idempotent (stable `doc_id` → upsert). Durable queue is a follow-up.

## Testing

- `extract.py`: unit tests per format (txt/md/html/csv inline; docx/pdf via tiny fixtures; unsupported → skip).
- `store.py`: connection CRUD + job lifecycle + activity log + Redis-down degradation (fakeredis/mocks).
- `sharepoint.py`: Graph parsing from canned responses; 401/error → `[]`.
- `sync.py`: file→SourceDoc mapping + IngestPipeline called per file + job counters (fake connector + fake pipeline).
- `MetricsStore`: INCR/PFADD windowing.
- API: each `/admin/*` route — admin-key gate (403), happy path, degraded path.
- Frontend: builds + typechecks; admin-key gate + sources flow render.

## Out of scope (v1)

Scheduled/background recurring sync · real per-file SharePoint ACLs · pptx/xlsx/image
extraction · Surfaces/Permissions/Developer functionality · Entra admin RBAC ·
purge-indexed-docs-on-disconnect · token streaming.

## Known blockers / follow-ups

- **Graph grant:** SharePoint returns zero files until `Sites.Read.All` + `Files.Read.All`
  are consented on the SubStrateOS app (`b170c2f3-…`). Same blocker as Live Fetch.
- **Durable sync queue** (survive container restart).
- **Per-file ACL** mapping from SharePoint permissions.
- **Entra admin RBAC** to replace the shared admin key.
