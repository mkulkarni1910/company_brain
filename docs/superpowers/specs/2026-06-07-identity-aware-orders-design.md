# Identity-Aware Order Lookup — Design

**Date:** 2026-06-07
**Status:** Approved
**Builds on:** `2026-06-07-entra-approval-routing-design.md` (the user directory),
`2026-06-05-refund-experience-design.md` (the refund playbook)

## Problem

A known customer asks the bot "can you help me with my order" and gets
"please provide your name and order number" — even though the directory
already knows exactly who is asking. Identity is resolved for routing but
never reaches retrieval or the answer prompt: the generic Q&A path answers
as the anonymous bot user, and the refund engine retrieves on raw text only.

The bot should use the requester's identity (from the synced directory) to
fetch *their* order directly — and, for customers, must never surface
another customer's order.

## Decisions (user-confirmed)

1. **Scope: both paths.** The generic Slack Q&A path AND the refund engine
   receive requester identity. (Teams + web chat get the same hook in a
   later iteration — the injection point is surface-agnostic.)
2. **Own-orders restriction.** Customers only ever see their own orders;
   agents and managers see all. Enforced in code, backstopped in prompt.
3. **Match key: email.** Seed order docs carry the customer's real tenant
   email; identity↔order matching is exact on email, names are display-only.
4. **Supersedes** the previous "customers get no engine run" decision: the
   customer path now runs a read-only engine evaluation to pre-fill the
   support card. Status flow is unchanged (`routed_to_support`).

## Architecture

### Requester payload

`DirectoryUser` (existing) is the requester payload — no new model. A
requester is "known" when the sender's Slack email resolves through
`DirectoryService.resolve()`.

### Changed: `app/orchestrator/kernel.py`

`answer()` gains `requester: DirectoryUser | None = None`.
- Builds a `requester_note` string and passes it to
  `build_grounded_messages` (below).
- After ACL recheck, applies the own-orders filter (below) to the
  candidate list when `requester.role == "customer"`.

### Changed: `app/generation/prompts.py`

`build_grounded_messages(..., requester_note: str | None = None)` injects
the note as a system-level line after the skill prompt. Note content:

- All roles: `Requester: {display_name} ({email}), role: {role}.`
- Customer: + `'My order'/'my refund' refers to orders belonging to
  {email}. Never reveal another customer's order details to them; if
  asked, say you can only discuss their own orders.`
- Agent/manager: + `'My …' refers to them; they may ask about any
  customer's order.`

### New: `app/retrieval/order_scope.py` (the enforcement layer)

Pure functions, no I/O:

- `is_order_chunk(content) -> bool` — heuristic: contains `Order #<digits>`
  AND a `Customer:` line.
- `order_customer_email(content) -> str | None` — extracts the email from
  the `Customer: Name (email)` line (lowercased).
- `scope_order_chunks(cands, requester) -> list` — when
  `requester.role == "customer"`: drop order chunks whose embedded email
  ≠ `requester.email`; **fail closed** — an order-looking chunk with no
  parseable email is also dropped. Non-order chunks always pass.
  Agents/managers/None requester: passthrough.

Called from the kernel (generic path) and the refund engine. The prompt
rule is the backstop; this filter is the gate — double enforcement, same
philosophy as the ACL story.

### Changed: `app/workflows/engine.py`

`evaluate(text, *, user, requester: DirectoryUser | None = None)`:
- Order-retrieval query becomes `f"{text} customer {display_name} {email}"`
  when a requester is present (biases retrieval toward their order).
- Order hits pass through `scope_order_chunks` (customers only).
- Decision prompt's user message gains
  `Requester: {name} ({email}), role {role} — 'my order' refers to them.`

### Changed: `app/workflows/flow.py` (customer path)

`_route_to_support` runs `engine.evaluate(text, user=user, requester=record)`
before posting:
- `decision.found` → support card is pre-filled with the order facts,
  `run.decision = decision` saved, audit event **"Order fetched"** with
  the facts line.
- Engine error or not-found → today's bare hand-off card; routing is
  never blocked by the lookup.
- Status remains `routed_to_support` in all cases.

`customer_request_blocks` gains an optional `decision` parameter that
renders the facts fields (reusing `_facts_fields`).

### Changed: `app/api/bots.py` (Slack generic path)

The `_reply` closure resolves the sender once
(`_slack_profile` → email → `directory.resolve()`, via a new
`directory=Depends(get_directory_service)` dependency) and passes the
record as `requester=` to `orchestrator.answer()`. Resolve failure ⇒
`requester=None` (today's anonymous behavior). The refund-workflow branch
already resolves identity inside the flow — unchanged.

### Seed: `scripts/seed_refund_demo.py`

Priya's order (#48213) carries her real tenant email
(`priya@OmkarConsultancy1910.onmicrosoft.com`) in the existing
`Customer: Name (email)` line format. Marcus Lee (#48190) stays fictional —
he is only referenced by agents and demos the staff "any customer" case.
Re-run is idempotent; one re-seed against prod after deploy.

## Failure behavior

| Failure | Behavior |
|---|---|
| Directory resolve fails in generic path | answer proceeds anonymous (today's behavior) |
| Engine fails on customer path | bare hand-off card, routing proceeds |
| Order-looking chunk lacks parseable email | dropped for customers (fail closed), kept for staff |
| Requester has no email in Slack profile | `requester=None` — anonymous answer |

## Testing

- `tests/test_order_scope.py` — detection heuristic, non-order passthrough,
  drop-other-customer, fail-closed unparseable, staff/None passthrough.
- Engine: requester augments the retrieval query and decision prompt
  (fake LLM captures messages); customer scoping applied to order hits.
- Flow: customer card enriched when `found`; degrades to bare card on
  engine error; "Order fetched" audit event; status `routed_to_support`.
- Kernel: `requester_note` present in built messages; customer filter
  applied post-ACL (fake retriever/LLM).
- Bots: Slack generic path passes `requester` when directory resolves,
  `None` when it doesn't.

## Out of scope

- Teams + web-chat requester injection (same hook, later iteration).
- Per-order ACL principals in the index (Approach B — rejected as
  over-engineering for the current corpus).
- A "claim" button on the enriched support card.

## North-star fit

**Check** deepens: the brain now knows *who* is asking and scopes what it
will say accordingly — identity-grounded answers, enforced in code and not
just in prompt. The support hand-off gets smarter (card pre-filled with the
customer's own order), the audit trail gains "Order fetched", and nothing
acts without the existing human gates.
