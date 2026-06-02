# Teams Private Channels + Real Per-Channel ACL — Design Spec (fast-follow)

**Date:** 2026-06-02
**Status:** Queued (fast-follow to `2026-06-02-teams-connector-design.md`)
**Why:** v1 ingests Teams with `{tenant}:everyone` ACL, so private channels can't be
ingested safely (they'd leak to all brain users). This spec adds real ACLs so private
(and shared) channels can be indexed without over-sharing.

## Goal
Ingest **private/shared** Teams channels, attaching each message's `acl_principals`
from the channel's actual membership, and enforce it at query time against the asking
user's real identity.

## The core problem
Query-time ACL only protects content if (a) docs are ingested with principals that
reflect real membership, and (b) the asking brain user resolves to matching principals.
Today both are stubbed (`{tenant}:everyone` + `PILOT_SINGLE_TENANT` grants everyone).

## Work
1. **Channel membership → principals (ingest side).**
   - For each private/shared channel, call Graph `GET /teams/{id}/channels/{cid}/members`
     (needs `ChannelMember.Read.All`, also a protected API) → member user ids (Entra oids).
   - Ingest each message with `acl_principals = [<member oid>, …]` (or the backing
     group/team id for standard channels). Standard channels keep team-wide/everyone scope.
   - New connector method `channel_acl(team, channel) -> list[str]`; thread into `_parse_messages`.
2. **Brain user identity → principals (query side).**
   - Map the authenticated brain user to their Entra **oid + group ids** (Easy Auth /
     bearer claims already carry oid; expand groups via Graph `memberOf` — `_expand_groups`
     exists but is fail-soft today). Ensure the AI Search ACL filter + ACLStore recheck
     compare the user's oid/groups against the doc `acl_principals`.
   - **Turn off / scope `PILOT_SINGLE_TENANT` for connected-org content** so `t-eval:everyone`
     no longer blankets private docs. This is the key safety change.
3. **Per-org isolation.** Decide tenant model for connected-org content (per-tenant index
   `brain-content-{tenant}` or tenant-scoped doc keys) so org A's private content is never
   queryable by org B. (Ties into the deferred I3 per-tenant-index item.)
4. **Permissions/infra.** Add `ChannelMember.Read.All` (+ `Channel.Read.All`) app perms +
   protected-API approval covers them.

## Out of scope
Real-time membership change sync (re-crawl picks up changes); message-level overrides.

## Risks
- Membership resolution at message granularity is expensive — cache per (team,channel).
- Getting ACL wrong = data leak; this spec must ship with tests proving a non-member
  user cannot retrieve a private-channel doc (query-time denial test).
