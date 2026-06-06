# Entra-Driven Approval Routing + User Directory — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** `2026-06-05-refund-experience-design.md`

## Problem

The refund playbook routes every approval to a single hardcoded Slack user
(`SLACK_REFUND_APPROVER_ID`). Real organizations encode who-approves-what in
their directory: Tom's manager is Diane in Entra ID, Diane is in the
`Managers` group, Tom is in `Support Agent`. The playbook should *derive*
the approver from org structure, enforce that only managers approve, and
treat everyone outside those groups as a customer.

Slack member IDs and Entra identities are joined by email. Hitting the
Slack and Graph APIs on every request is slow and rate-limited, so we
maintain a synced user directory in Redis, refreshed daily by a scheduler.

## Decisions (user-confirmed)

1. **Customer path:** customers' refund requests are posted to the
   `#refunds` channel for Support Agents to pick up — no engine run.
2. **Approver:** the requester's Entra manager, who must be in the
   `Managers` group and resolvable to a Slack ID.
3. **No fallback approver:** if the manager is unusable, **stop the run**
   (`needs_attention`) — the playbook "stops if unsure" rather than
   guessing. `SLACK_REFUND_APPROVER_ID` is removed.
4. **Directory cache:** unified Slack + Entra records (email, Slack ID,
   manager email, groups, derived role) in Redis; request-time routing
   reads only the cache, with a live write-through fallback on miss.
5. **Scheduler:** in-app asyncio loop in FastAPI lifespan, daily, first
   run shortly after startup. (Per-replica; idempotent upserts make
   multi-replica redundancy harmless.)

## Roles

Derived from Entra group display names (configurable), precedence
manager > agent > customer:

| Entra group | Role | Capabilities |
|---|---|---|
| `Managers` (`entra_managers_group`) | `manager` | approve/reject; may request (routes to their own manager) |
| `Support Agent` (`entra_agents_group`) | `agent` | request refunds on customers' behalf |
| neither / unknown to Entra | `customer` | request → routed to support channel |

## Architecture

### New module: `app/directory/`

**`store.py` — `DirectoryStore`** (mirrors `RunStore`: Redis + in-process
fallback):

```
directory:user:{email_lower}  → JSON record
directory:emails              → set of known emails
directory:slack:{slack_id}    → email (reverse index for interactive handler)
```

Record: `{email, slack_id, display_name, entra_id, manager_email,
groups[], role, synced_at}`. No TTL — daily sync refreshes; stale beats
missing.

API: `get_by_email`, `get_by_slack_id`, `upsert`, `list_all`,
`resolve(email)` — `resolve` does the live fallback (Slack
`users.lookupByEmail`, then Graph `GET /users/{email}?$expand=manager`
and `GET /users/{id}/memberOf` filtered to the two role-group names)
and writes through on success. Email unknown to Entra but
known to Slack ⇒ role `customer`.

**`sync.py` — `DirectorySync.run()`** — idempotent, fail-soft:

1. Slack: new paginated `users.list` wrapper (skip bots/deleted/USLACKBOT)
   → `{email → slack_id, display_name}`.
2. Entra (via existing `graph_token()`):
   - `GET /users?$select=id,displayName,mail&$expand=manager($select=mail)`
   - `GET /groups?$filter=displayName eq '<group>'` +
     `/groups/{id}/members` — two calls for the two role groups.
3. Merge on lowercase email; derive role; upsert all; return summary
   `{slack_users, entra_users, matched, managers, agents, customers, errors}`.
4. Partial failure keeps old data — never wipe on error.

### New: `app/scheduler.py`

`start_periodic(name, coro_fn, interval_hours, run_at_start=True) ->
asyncio.Task`. Started in lifespan, cancelled on shutdown, exceptions
logged + loop continues. Consumer today: `directory_sync` @ 24h
(`directory_sync_interval_hours`), first run ~10s post-startup. Future
consumer: Outlook subscription renewal (closes that spec's "needs cron"
gap).

### Changed: `app/workflows/flow.py` (`RefundFlow`)

Request-time flow:

1. Slack user ID → email (`users.info`, existing) → `directory.resolve`.
2. Branch on role:
   - **customer** → post summary card to `slack_refund_channel_id`
     ("needs a support agent"); run outcome `routed_to_support`; no
     engine evaluation. Channel unset ⇒ `needs_attention` + requester
     told to contact support.
   - **agent/manager** → engine evaluates as today. Auto-approve path
     unchanged. Needs-approval: requester's `manager_email` →
     directory record must (a) exist, (b) role `manager`, (c) have
     `slack_id`. All hold ⇒ DM approval card to that manager. Any fail
     ⇒ run `needs_attention`, audit reason, requester notified.
3. Audit events narrate routing: *"Requester Tom (Support Agent) →
   approval routed to Diane (Tom's manager, Managers group)"*.

### Changed: interactive handler (`/bot/slack/interactive`)

On Approve/Reject: clicker must be the routed approver **and** directory
role `manager`. Otherwise ephemeral "Only the routed approver (a manager)
can act on this" + audit event recording the attempt.

### Changed: `app/bots/slack.py`

Add paginated `users.list` method; new customer-routing card builder in
`refund_cards.py`.

### Admin surface (`app/api/admin.py`)

- `POST /admin/directory/sync` — manual trigger, returns sync summary.
- `GET /admin/directory` — list records, emails redacted (`t***@domain`).
Both behind existing admin-key auth.

## Edge cases

| Case | Behavior |
|---|---|
| Requester email missing from Slack profile | `needs_attention`, "could not establish requester identity" |
| Directory miss + live fallback: Slack knows them, Entra doesn't | role `customer` |
| Directory miss + Slack lookup fails | `needs_attention` |
| Manager absent / not in Managers group / no Slack ID | stop the run (`needs_attention`) |
| Manager requests a refund | same rule — routes to *their* manager; Diane (no manager) would stop. Accepted. |
| Non-approver or non-manager clicks Approve | ephemeral denial + audit event |
| Sync API failure | old data retained; error in summary; next tick retries |

## Config (`app/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `entra_managers_group` | `Managers` | group → `manager` role |
| `entra_agents_group` | `Support Agent` | group → `agent` role |
| `slack_refund_channel_id` | unset | customer-request channel |
| `directory_sync_interval_hours` | `24` | sync cadence |
| `slack_refund_approver_id` | **removed** | replaced by directory routing |

## External prerequisites (one-time, user actions)

- Graph app permissions on the existing app registration:
  `User.Read.All`, `GroupMember.Read.All` (application, admin consent).
- Slack scopes `users:read`, `users:read.email` (verified present).
- Set `slack_refund_channel_id` on the container app; remove
  `SLACK_REFUND_APPROVER_ID` env + any stale secret.

## Testing

`tests/test_directory.py`, `tests/test_refund_routing.py` (fakes/respx
per suite conventions):

- role derivation + merge (Slack-only, Entra-only, both, precedence)
- store round-trip, Redis fake + in-process fallback
- routing: customer→channel, agent→manager DM, manager-missing→stop,
  manager-not-in-group→stop, channel-unset→needs_attention
- interactive enforcement: routed approver OK; agent click denied;
  non-routed manager denied
- sync fail-soft: mid-sync 500 leaves prior records intact
- scheduler: tick fires, survives exception (injected fast interval)
- update existing refund tests that stub `SLACK_REFUND_APPROVER_ID`

## Out of scope (this iteration)

- "Claim" button on the customer→channel card (agent re-runs the flow
  manually).
- Multi-workspace Slack; nested-group precedence beyond the two named
  groups; per-replica scheduler election.
- Web UI changes — Runs page already renders the new audit events.

## North-star fit

When → **Check** (identity + role from the directory) → **Stop** (only a
real manager may approve; halt when org data can't name one) → Do →
**Record** (routing decisions audited). Strengthens "known identity" and
"human approval" pillars; the directory becomes shared infrastructure for
every surface.
