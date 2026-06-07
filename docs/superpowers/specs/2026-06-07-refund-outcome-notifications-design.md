# Refund Outcome Notifications — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** `2026-06-07-identity-aware-orders-design.md` (customer routed runs
carry a pre-fetched decision), `2026-06-07-entra-approval-routing-design.md`
(directory), `2026-06-05-refund-experience-design.md` (the playbook).

## Problem

When Diane approves (or rejects) a refund, only the agent's thread hears about
it — as a passive "Refund approved by Diane". Two gaps:

1. The requesting agent isn't @-mentioned, so nothing pings Tom to act.
2. The customer (Priya) — who started the whole thing in her own conversation
   and was told "someone will follow up here" — never hears the outcome.

## Decisions (user-confirmed)

1. **Customer link: by order, with DM fallback.** At decision time, find the
   customer's `routed_to_support` run with the same `order_id` and reply in
   that exact thread; close that run out. If no linked run exists, DM the
   customer via the directory. If neither path works, skip — with an audit
   event saying so.
2. **Rejection reason: auto-composed from policy facts only.** No internal
   language — never mention approvals, exceptions, or managers to the
   customer ("it falls outside our refund policy ($500 within 30 days)").
3. **Agent @-mention on BOTH outcomes** — approved and rejected.

## Architecture

### Changed: `app/domain/workflow.py`

`RefundRun` gains `handoff_channel: str | None = None` and
`handoff_ts: str | None = None` — the support-channel hand-off card's
location, captured when the customer's request is routed (the
`chat.postMessage` response already returns `ts`; today it is discarded).

`RefundDecision` gains `customer_email: str | None = None`. The seeded order
docs carry `Customer: Name (email)`; the engine extracts it when present.
Optional → fully backward-compatible with stored runs.

### Changed: `app/workflows/engine.py`

`DECISION_PROMPT`'s JSON schema gains `"customer_email": "..."` with the
instruction to copy the customer's email from the order record when present,
else null.

### New: `RunStore.find_routed_run(order_id)` (`app/workflows/store.py`)

Returns the most recent run with `kind == "refund"`,
`status == "routed_to_support"`, and `decision.order_id == order_id`
(scans `list_runs(limit=100)`); `None` otherwise. Because notification flips
the linked run's status, a second Approve click finds nothing — natural
idempotency on top of the existing `pending_approval` guard.

### Changed: `app/workflows/flow.py` (`_route_to_support`)

The hand-off post's response `ts` is persisted onto the customer's run as
`handoff_channel` / `handoff_ts` before the run is saved.

### Changed: `app/workflows/flow.py` (`handle_action` outcome section)

After the decision is recorded:

1. **Agent channel post** (existing `outcome_blocks` post) becomes
   "Hello <@{run.requester_slack_id}> — refund {approved|rejected} by
   {approver}" — a real Slack mention; falls back to the plain requester name
   when `requester_slack_id` is absent. Applies to both outcomes.
2. **`_notify_customer(token, run, approved)`** — fail-soft, never blocks or
   raises into the decision path:
   - `linked = store.find_routed_run(decision.order_id)`; if found and it has
     a channel: post the customer outcome card to `linked.channel` /
     `linked.thread_ts`; set `linked.status` to `completed` (approved) or
     `rejected`; add event "Outcome relayed — {approved|rejected} by
     {approver}" to the linked run.
   - Else: `decision.customer_email` → `directory.resolve()` →
     `conversations.open` → DM the same card.
   - Else: skip.
   - If the linked run has `handoff_channel`/`handoff_ts`, thread one line
     under the hand-off card in the support channel: "✅ Resolved — approved
     by {approver}, customer notified" / "✕ Resolved — rejected by
     {approver}, customer notified" (suffix "customer not reachable" when the
     notification was skipped). Fail-soft.
   - The deciding run always gets one audit event: "Customer notified"
     (with where) or "Customer not reachable".

### Changed: `app/bots/refund_cards.py`

- `outcome_blocks(..., mention: str | None = None)` — header line becomes
  "Hello {mention} — {verdict} by {approver}".
- New `customer_outcome_blocks(d, *, approved)`:
  - Approved (green bar): "Hello {first name} — good news! Your refund of
    {amount} for order #{id} has been approved and is being processed."
  - Rejected (red bar): "Hello {first name} — we couldn't process your refund
    for order #{id}: it falls outside our refund policy ({limit} within
    {days} days). Please reach out to our support team if you have questions."
  - Customer-facing copy NEVER references managers, approvals, or exceptions.

### Wiring

`RefundFlow` already holds `directory`; no new dependencies. `handle_action`
is the only caller of the new pieces.

## Failure behavior

| Failure | Behavior |
|---|---|
| No linked run, no customer_email | skip + "Customer not reachable" audit event |
| Email doesn't resolve in directory/Slack | same skip + audit |
| Slack post to customer thread fails | audit notes failure; decision path unaffected |
| Linked run already closed (double click) | `pending_approval` guard catches first; finder only matches `routed_to_support` — no double-notify |
| Old runs without `customer_email` | DM fallback unavailable; thread link still works |

## Testing

- Store: `find_routed_run` match, recency (latest wins), status + kind + order
  filtering, None on miss.
- Flow approve: agent post contains `<@U_TOM>`; customer thread receives the
  approved card; linked run → `completed` with "Outcome relayed" event;
  deciding run gets "Customer notified".
- Flow reject: rejected card wording — asserts policy facts present AND no
  "exception"/"manager"/"approv" substring in the customer text.
- DM fallback: no linked run + customer_email resolves → `conversations.open`
  + DM card.
- Skip path: no link, no email → no customer post, "Customer not reachable"
  event, decision flow completes normally.
- Hand-off card: `_route_to_support` persists `handoff_channel`/`handoff_ts`;
  on decision, a resolution line is threaded under the card; absent handles
  (legacy runs) skip silently.
- Engine: fake LLM returns `customer_email`; decision carries it.
- Mention fallback: run without `requester_slack_id` uses plain name.

## Out of scope

- Manager-typed rejection reasons (Slack modal) — future.
- Teams parity for outcome notifications.
- Notifying when the AGENT's run was for a customer who asked elsewhere
  (cross-surface linking beyond order_id).

## North-star fit

**Do → Record** completes the loop: the human decision now reaches every
surface it touched — the agent is pinged to act, the customer hears the
outcome where they asked, the approver's card flips, and the support
channel's hand-off card is marked resolved — and both runs' audit trails
record the relay. The customer never
sees internal mechanics, only the policy-grounded outcome.
