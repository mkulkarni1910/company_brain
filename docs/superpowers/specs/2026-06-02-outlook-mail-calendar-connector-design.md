# Outlook Mail + Calendar Connectors (org-wide, per-participant ACL) — Design Spec

**Date:** 2026-06-02
**Status:** Approved
**Builds on:** `2026-06-02-sharepoint-admin-consent-oauth-design.md` (reuses its app + flow) and `2026-06-02-teams-connector-design.md` (unified `collect_documents` runner)

## Goal

Let an admin connect their org's Microsoft 365 (same admin-consent flow as
SharePoint/Teams) and index **all users' Outlook mail and calendar** into the
intelligence layer — with **per-participant ACLs** so a user only ever retrieves
mail/events they sent or received. Data stays fresh via **realtime Graph
webhooks**, backed by a **delta-query reconcile** safety net.

## Decisions (locked)

| Decision | Choice |
| --- | --- |
| Connectors | **Two**: `outlook_mail`, `outlook_calendar` — independently connectable/toggleable. |
| Auth | **Reuse** the multi-tenant Entra app + admin-consent OAuth (SharePoint/Teams). Add Graph **application** perms `Mail.Read`, `Calendars.Read`, `User.Read.All`. Standard perms — **no protected-API blocker** (unlike Teams). |
| Scope | **Org-wide**: enumerate all users via `/users`, crawl every mailbox/calendar. |
| ACL | **Per-participant**, via the existing `acl_principals` enforcement (`build_acl_filter` → `acl_principals/any(...)` vs `user.principals()`). No new query-side code. |
| ACL resolution | **Mailbox-ownership, not address mapping.** Each item is stamped with the **mailbox owner's Entra object ID** (known from `/users`). Crawling all mailboxes means every participant's copy is stamped to them. |
| Dedup (A) | **Dedup by `internetMessageId` (mail) / `iCalUId` (calendar)**: one doc, `acl_principals` = **union** of all internal owners holding it. Avoids duplicate hits + N× storage. |
| Delivery | **Realtime webhooks** (freshness) **+ delta-query reconcile** (authoritative safety net + backfill). Both feed one idempotent ingest path. |
| Maintenance (B) | **Admin endpoint `POST /admin/connections/maintain` + external cron.** Runs subscription renewal **and** the delta reconcile sweep. Safe under ACA scale-to-zero / multi-replica. |
| Content | **Body text only** — no attachments (fast-follow). |

## ACL mechanism (the crux)

Graph message/event payloads expose **SMTP addresses**, but `user.principals()`
matches **Entra object IDs**. We avoid any address→ID mapping by using **mailbox
ownership**: because we crawl every mailbox/calendar org-wide, each participant's
own copy is stamped with that mailbox owner's object ID (from `/users`
enumeration). A user then retrieves exactly the mail/events they are a party to.
External participants have no crawled mailbox, so they never appear as
principals — correct, since only internal users query the brain.

**Dedup + ACL union:** the same email exists in N mailboxes (sender + recipients).
`doc_id` is keyed on the stable cross-mailbox identifier (`internetMessageId` for
mail, `iCalUId` for events). When the same id is seen again — within a crawl pass,
or later via webhook/delta — its `acl_principals` is the **union** of all internal
owners holding it. Webhook/delta updates do a read-modify-write union (read
existing principals from `ACLStore`, union the new owner, re-set).

## Architecture

### Shared Graph helpers — `app/connectors/graph.py` (new; targeted cleanup)
- `async graph_token(tenant_id) -> str` — client-credentials against the connected
  tenant (extracts the `_token()` body currently duplicated in `sharepoint.py`,
  `teams.py`; those refactor to call it; behaviour unchanged).
- `async graph_get_json(token, url) -> dict` — shared GET helper.
- `list_users(token) -> list[{user_id, mail, upn}]` — `GET /users?$select=id,mail,userPrincipalName` (paged). Needs `User.Read.All`.

### `app/connectors/outlook_mail.py` — `OutlookMailConnector(tenant_id)`
- Module-level pure parsers (unit-tested), mirroring `teams.py`:
  - `_strip_html`, `_dt` (shared style).
  - `_parse_messages(data, owner_oid, brain_tenant) -> list[SourceDoc]` — per message:
    `doc_id=f"outlookmail:{internetMessageId}"`, `source="outlook_mail"`,
    `title=subject`, `body=_strip_html(body.content)` (fallback `bodyPreview`),
    `author_id=from.emailAddress.address`, `acl_principals=[owner_oid]`,
    `created_at/modified_at` from `receivedDateTime`/`lastModifiedDateTime`,
    `source_url=webLink`, `mime="text/plain"`. Skip empty bodies.
- `collect_documents(cap)` — BFS users → messages
  (`GET /users/{id}/messages?$select=subject,bodyPreview,body,from,toRecipients,ccRecipients,internetMessageId,receivedDateTime,lastModifiedDateTime,webLink`,
  paged via `@odata.nextLink`, capped). Accumulate into a `dict[internetMessageId]
  -> SourceDoc`, unioning `acl_principals` across owners. Returns `CollectResult`.
  Never raises; degrades to partial/empty on 403/error.
- `delta(user_id, token|None) -> (docs, new_delta_link)` — `GET /users/{id}/mailFolders('inbox')/messages/delta`
  (or `?$deltatoken=`), parse like above, return new `@odata.deltaLink`.

### `app/connectors/outlook_calendar.py` — `OutlookCalendarConnector(tenant_id)`
- `_parse_events(data, owner_oid, brain_tenant) -> list[SourceDoc]`:
  `doc_id=f"outlookcal:{iCalUId}"`, `source="outlook_calendar"`, `title=subject`,
  `body` = `subject + agenda(body) + location + attendee names + start–end`,
  `author_id=organizer.emailAddress.address`, `acl_principals=[owner_oid]`,
  timestamps from `start`/`lastModifiedDateTime`, `source_url=webLink`.
- `collect_documents(cap)` — BFS users → `GET /users/{id}/calendarView?startDateTime=…&endDateTime=…&$select=subject,body,start,end,location,organizer,attendees,iCalUId,lastModifiedDateTime,webLink`
  over a bounded window (`outlook_calendar_past_days`/`future_days`). Dedup by
  `iCalUId` with ACL union. `CollectResult`. Never raises.
- `delta(user_id, token|None)` — `GET /users/{id}/calendarView/delta` over the window.

### Realtime — `app/connectors/subscriptions.py` + webhook route
- `create_subscription(tenant_id, user_id, resource)` — `POST /subscriptions`
  `{changeType:"created,updated,deleted", resource:"/users/{id}/messages"|"/users/{id}/events",
  notificationUrl:"{brain_api_base_url}/admin/connections/webhook",
  expirationDateTime: now+subscription_ttl_minutes, clientState:<graph_webhook_client_state>}`.
- `SubscriptionStore` (Redis, `subscriptions:{tenant}` hash) — `SubscriptionRecord`
  `{subscription_id, tenant_id, connection_id, user_id, resource, expiration, provider}`.
  put / list / delete / list_expiring(before).
- Webhook route on the **key-free `callback_router`** (`POST/GET /admin/connections/webhook`):
  - **Validation handshake:** `?validationToken=…` → return it as `text/plain` 200 (within 10s).
  - **Notifications:** verify `clientState` (reject mismatches); for each change derive
    `(user_id, resource_id)`, fetch the resource via Graph, build `SourceDoc`
    (owner ACL + dedup union), `IngestPipeline.process`; `deleted` → drop from index +
    `ACLStore`. Respond `202` immediately; do work detached (`asyncio.create_task`).

### Maintenance — `POST /admin/connections/maintain` (admin-key)
Idempotent pass an external cron calls on an interval. For each live
`outlook_*` connection:
1. **Renew** subscriptions expiring within a threshold (`PATCH /subscriptions/{id}`).
2. **Reconcile** new joiners (create subs) / leavers (delete subs) vs `/users`.
3. **Delta sweep**: per user, run `delta(user_id, stored_token)`, ingest changes,
   persist the new delta link (stored in Redis per `(connection, user, resource)`).
This is the safety net for dropped/expired notifications and the backfill driver.

### Wiring (small — reuses Teams generalization)
- `Connection.type` Literal `+= "outlook_mail", "outlook_calendar"`.
- `oauth_connect` provider allow-list `+= {"outlook_mail","outlook_calendar"}`;
  callback `display` map extended. Callback already builds the `Connection`
  generically (`site_id=tenant`, `web_url=""`) — no other change. After the
  initial `SyncRunner.run`, the callback also kicks off subscription creation for
  that provider's resource (detached).
- `connector_for(conn)` → map the two new types to their connectors.

### Config (`app/config.py`)
`outlook_calendar_past_days=90`, `outlook_calendar_future_days=365`,
`outlook_max_per_user=200`, `graph_webhook_client_state` (secret),
`subscription_ttl_minutes=4230` (~Graph max), `subscription_renew_threshold_minutes=720`.
`brain_api_base_url` **must be publicly reachable** for webhooks (the ACA URL is).

### Frontend
- Outlook Mail + Outlook Calendar become **connectable** cards (drop "Coming soon");
  toggles → `connectProvider("outlook_mail"|"outlook_calendar")`.
- Per-provider connection lookup map (same generalization Teams needs).
- `web/public/logos/outlook.svg`.

## Data flow
Toggle Outlook Mail → connect(provider=outlook_mail) → admin consent → callback →
`Connection{type=outlook_mail}` → `SyncRunner` → `collect_documents` (backfill,
dedup+union) → `IngestPipeline` → live + create per-user `/messages`
subscriptions. Thereafter: new mail → Graph webhook → fetch → ingest (idempotent
union). Periodically: cron → `/maintain` → renew subs + delta reconcile.

## Error handling
- Any Graph error / 403 → connectors degrade to partial/empty `CollectResult`
  (never raise); connection still goes live (0 or partial items).
- Webhook: bad `clientState` → 202 ignore (no work); validation handshake always echoes.
- Subscription create/renew failure → logged; delta sweep covers the gap.
- Bad/expired OAuth state → callback 302 `?error=oauth`.
- Cap hit → `CollectResult.truncated` → `job.truncated`.

## Testing
- Pure parsers: `_parse_messages`/`_parse_events` (html→text, skip empty, field map,
  owner-ACL stamping); dedup + ACL **union** across owners.
- `collect_documents` against a fake Graph: paging, cap/truncation, dedup.
- `delta`: token round-trip, returns new delta link, parses changes.
- Webhook: validation handshake echoes `validationToken`; notification → fetch →
  ingest (fake pipeline); `clientState` mismatch is ignored.
- `SubscriptionStore`: put/list/delete/list_expiring round-trips.
- `/maintain`: renews expiring, adds joiner / removes leaver, runs delta (fakes).
- `oauth_connect`/`connector_for`: the two new providers resolve; bad provider → 400.
- Existing SharePoint/Teams + `graph_token` refactor: all prior tests stay green.
- Manual: admin-consent → backfill ingests → send a test mail → searchable within
  seconds → only participants can retrieve it; non-participant cannot.

## Scale note (acknowledged)
Org-wide realtime ≈ **(#users × 2) subscriptions**, each renewed ≤3 days, plus a
public webhook — against Graph's per-app/per-tenant subscription limits. Fine for a
pilot; the delta reconcile is the backstop if subscriptions lapse. Revisit batching
/ change-notification scaling before a 10k-user tenant.

## Out of scope (v1) → fast-follow
Attachments (extract + link); shared / room / resource mailboxes; explicit
distribution-list ACL expansion (mailbox-ownership covers internal members
implicitly); encrypted rich-notification payloads (we fetch-on-notify instead);
mail folders beyond the default scope.
