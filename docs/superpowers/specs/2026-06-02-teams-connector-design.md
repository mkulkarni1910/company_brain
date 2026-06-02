# Microsoft Teams Connector (org channel-message sync) — Design Spec

**Date:** 2026-06-02
**Status:** Approved
**Builds on:** `2026-06-02-sharepoint-admin-consent-oauth-design.md` (reuses its app + flow)

## Goal

Let an admin connect their org's Microsoft Teams (same admin-consent flow as
SharePoint) and crawl all **standard (public) channel** messages into the
intelligence layer.

## Decisions (locked)

| Decision | Choice |
| --- | --- |
| Auth | Reuse the multi-tenant Entra app + admin-consent OAuth from the SharePoint connector. Add Graph app perm `ChannelMessage.Read.All`. |
| Channel scope | **Standard (public) channels only.** Private/shared channels deferred to a fast-follow spec (need real per-channel-membership ACLs). |
| Doc granularity | **One `SourceDoc` per message** (channel/team in the title), capped by `connector_max_items`. |
| ACL | Pilot model: `tenant=t-eval` + `{t-eval}:everyone`. (Safe for standard channels in the single-tenant pilot.) |
| Connect endpoint | **Generalize** the SharePoint connect/callback to a `provider ∈ {sharepoint, teams}` param (no duplicate endpoints). |
| SyncRunner | **Unify** via a `collect_documents(cap) -> list[SourceDoc]` connector interface; one provider-agnostic runner. |

## Architecture

### Generalize the OAuth connect/callback (was SharePoint-only)
- `POST /admin/connections/connect?provider=<sharepoint|teams>` (admin-key) → store a
  one-shot state that records **both** brain tenant and `provider`; return the consent URL.
  (Keep `…/sharepoint/connect` as a thin alias → provider=sharepoint for back-comp.)
- `GET /admin/connections/callback` (key-free; state-validated) → consume state →
  `(brain_tenant, provider)` → create `Connection{type=provider, connected_tenant_id}` →
  run the right crawler via the unified `SyncRunner` → 302 to `…/admin/sources?connected=<provider>`.
- OAuth state store: `put_oauth_state(state, tenant, provider)` / `consume_oauth_state(state) ->
  (tenant, provider) | None`. (Extend the existing Cosmos + Redis state methods; store provider in the JSON.)

### Connector interface (unify runner)
- New Protocol (duck-typed): `async def collect_documents(self, cap: int) -> list[SourceDoc]`.
- `SharePointConnector.collect_documents` — wraps existing list_files + fetch_content +
  extract_text into SourceDocs (refactor; behaviour unchanged).
- `SyncRunner.run(connection)` — builds the connector for `connection.type` +
  `connected_tenant_id`, calls `collect_documents(cap)`, ingests each via `IngestPipeline`,
  updates job counters, sets the connection live/error. (Drop the SharePoint-specific
  fetch/extract loop from the runner — it moves into the connector.)
- A `connector_for(conn)` factory maps `conn.type` → `SharePointConnector`/`TeamsConnector(tenant_id=conn.connected_tenant_id)`.

### `app/connectors/teams.py` — `TeamsConnector(tenant_id)`
- `_token()` — client-credentials for the connected tenant (same as SharePointConnector; share a small `_graph_token(tenant_id)` helper or duplicate the ~8 lines).
- `list_teams()` — `GET /groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')&$select=id,displayName` (paged). Returns `[{team_id, name}]`.
- `list_channels(team_id)` — `GET /teams/{id}/channels?$select=id,displayName,membershipType`; keep only `membershipType == "standard"`.
- `list_messages(team_id, channel_id)` — `GET /teams/{id}/channels/{cid}/messages` (paged via `@odata.nextLink`), up to the remaining cap.
- `collect_documents(cap)` — BFS teams → standard channels → messages → `SourceDoc`:
  `doc_id=f"teams:{team_id}:{channel_id}:{message_id}"`, `source="teams"`,
  `title=f"{team_name} / {channel_name}"`, `body=<plain text of message.body.content>`
  (strip HTML when `contentType==html`; skip empty/system messages),
  `author_id=message.from.user.id`, `created_at/modified_at` from `createdDateTime`,
  `acl_principals=["{tenant}:everyone"]`, `mime="text/plain"`. Cap total; degrade to `[]` on error/403.

### Infra
- Add Graph application permission **`ChannelMessage.Read.All`** to app `19487212-…` + consent (direct appRoleAssignment, like Sites/Files).
- **Hard blocker:** this is a Microsoft **protected API**. App-only channel-message read 403s until the tenant is approved via Microsoft's protected-APIs request (support ticket / form) AND, for export at scale, a metered payment model is configured (see "Export content with the Microsoft Teams Export APIs": https://learn.microsoft.com/en-us/microsoftteams/export-teams-content). Approval is per-tenant. Until then, Teams connects + goes "live" with 0 items (graceful). Wire it fully; lights up on approval.

### Frontend
- Teams becomes a **connectable** provider (not "Coming soon"). Its Enable Sync toggle →
  `connectProvider("teams")` → `POST /admin/connections/connect?provider=teams` → redirect.
- Status/items/last-sync come from the `type=="teams"` connection (extend the page's
  connection lookup from SharePoint-only to a per-provider map).
- Return handling already keys off `?connected=<provider>`.

## Data flow
Toggle Teams → connect(provider=teams) → consent → callback (state→teams) →
Connection{type=teams} → SyncRunner → TeamsConnector.collect_documents → per-message
SourceDoc → IngestPipeline → live + item_count.

## Error handling
- 403 (protected API not approved) / Graph errors → `collect_documents` returns `[]`;
  connection goes live with 0 items (or error if token fails). No crash.
- Bad/expired state → callback 302 `?error=oauth`.
- Cap hit → `job.truncated`.

## Testing
- state store: provider round-trips (put/consume returns tenant+provider).
- generalized connect: `provider=teams` → consent URL; bad provider → 400.
- callback: state(teams) → Connection type=teams; reuses existing valid/invalid tests.
- `TeamsConnector` pure parsers (`_parse_teams`, `_parse_channels` filters standard, `_parse_messages` HTML→text, skip system); `collect_documents` against a fake graph; degrade on error.
- unified `SyncRunner`: collect_documents → ingest → counters → live (fake connector).
- SharePoint refactor: existing SharePoint sync tests still green.
- Manual: admin-consent Teams against the M365 tenant → (post protected-API approval) messages ingest → answerable in chat.

## Out of scope (v1) — queued fast-follow
**Private/shared channels + real per-channel-membership ACL** (resolve channel members →
`acl_principals` per message; map brain Entra identities → those principals at query time).
Also: chat (1:1/group) messages, delta/incremental sync, reactions/attachments.
→ Separate spec: `docs/superpowers/specs/2026-06-0X-teams-private-channels-acl-design.md`.
