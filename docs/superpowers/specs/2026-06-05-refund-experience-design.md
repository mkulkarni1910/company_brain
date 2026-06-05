# Refund Experience Use Case — Design

**Date:** 2026-06-05
**Status:** Approved
**Source:** `/Users/lokesh/Desktop/MicrosoftHackthon/refund_experience_prototype.html` (9-step walkthrough)

## Goal

Turn the 9-page refund prototype into a working SubstrateOS use case: a support
agent (**Tom Reyes**) asks about a customer refund in Slack, SubstrateOS runs a
**refund skill** grounded in mocked AI Search data, hits a policy rule it cannot
auto-approve, routes the decision to Tom's manager (**Diana Foster**) as a Slack
DM with real **Approve / Reject** buttons, then posts the outcome back to the
channel — with every step recorded in an audit trail visible in the web app.

Test users for Tom and Diana will be created in Slack afterwards to replicate
the flow live; until then the flow is verified with simulated Slack payloads.

## Decisions made

| Question | Decision |
|---|---|
| Approval UX | Real Approve/Reject buttons via a new Slack interactivity endpoint |
| Web UI scope | Slack flow + a read-only **Runs/Audit** page (skip the animated live-run view) |
| Rule evaluation | Fully LLM-driven: one LLM call reads the retrieved order + policy docs and decides `auto_approve`, emitting structured JSON; code acts on it |
| Approver resolution | Config setting (`SLACK_REFUND_APPROVER_ID` = Diana's Slack member ID); approval card is DM'd to her |
| Architecture | Workflow-typed skill: optional `workflow` field on the Skill model; when the router resolves a skill with `workflow == "refund"`, the Slack bot diverts to a workflow engine instead of plain RAG |

## Demo script (target behaviour)

1. **Tom** in `#refunds`: `@SubstrateOS customer Priya Sharma is asking for a refund of $1,200 on order #48213. It's been about 45 days. Can we do it?`
2. Bot acks: *"On it — pulling up order #48213 and checking the refund policy…"*
3. Workflow retrieves the order doc + refund policy from AI Search; one LLM call emits a grounded structured decision → over limit.
4. Bot posts a **"⚠ Needs approval"** card in the channel: WHY ($1,200 > $500, 45d > 30d per refund-policy v3) + WHAT I'M DOING (routed to Diana Foster).
5. **Diana** receives a DM: approval card (customer, order, amount, age, requester, reason) with **Approve / Reject** buttons.
6. Diana clicks **Approve** → her card updates to *"✓ Approved by Diana Foster"*.
7. Channel gets: *"✅ Approved by Diana Foster — refund of $1,200 issued to Priya Sharma on order #48213. Full record in the audit log."* (Refund issuance is mocked — a terminal audit event, no payment integration.)
8. Web app → **Runs** page: run `#RB-xxxx` with the audit table (Time / Step / Detail / Who).
9. **Bonus path:** order **#48190** ($89, 12 days) auto-approves instantly — no Diana.

Reject path: Diana clicks **Reject** → card updates to "✗ Rejected", channel is
told the refund was declined, run status `rejected`.

## Components

### 1. Seeded data (`scripts/seed_refund_demo.py`)

Ingests mock docs through the existing ingest pipeline (auto chunk + embed,
`acl_principals: ["{tenant}:everyone"]`, `source: "uploaded"`):

- **`refund-policy-v3`** — Acme refund policy: auto-approve only when amount ≤ **$500** AND order age ≤ **30 days**; otherwise a **Support Manager** must approve before any refund is issued.
- **`order-48213`** — customer Priya Sharma, **$1,200**, placed ~45 days before seed date, delivered.
- **`order-48190`** — customer Marcus Lee, **$89**, placed ~12 days before seed date, delivered (auto-approve path).

Also creates the **`refund` skill** via the skill store (slug `refund`, team
`Support`, `workflow: "refund"`, description tuned so the LLM auto-router picks
it for plain-language refund questions). Script is idempotent (upserts).

### 2. Skill model extension

- `Skill`, `SkillCreate`, `SkillUpdate` gain optional `workflow: str | None = None`.
- `ResolvedSkill` carries `workflow` through the pipeline.
- No behaviour change for skills without a workflow.

### 3. Refund workflow engine (`app/workflows/refund.py`)

- Input: the user's message text, requester identity (Slack user id + display name), channel/thread.
- Retrieves order + policy chunks from AI Search (reusing the existing retrieval path with the bot identity).
- **One LLM call** with a structured-JSON instruction returns:
  `{order_id, customer, amount_usd, order_age_days, policy_limit_usd, policy_limit_days, auto_approve, reasoning}`.
- The LLM makes the decision (fully LLM-driven); code branches on `auto_approve`.
- Creates a run (`RB-xxxx`) and writes an audit event per step: request received, facts gathered, rule evaluated, routed for approval / auto-approved, decision, refund issued.

### 4. Run + approval store (`app/workflows/store.py`, Redis)

- `run:{id}` — JSON: id, status (`running | pending_approval | approved | rejected | completed`), requester (name + slack id), channel, thread_ts, dm_channel, dm_ts, facts JSON, reasoning, approver, timestamps.
- `run:{id}:events` — list of `{ts, step, detail, actor}` audit events.
- `runs:all` — index of run ids (most recent first).

### 5. Slack plumbing

- **Slack webhook** (`app/api/bots.py`): resolve the skill via the existing SkillRouter (same as `/query` does today — the bot currently bypasses it). If the resolved skill has `workflow == "refund"`, divert to the workflow path: immediate ack message → run engine → decision card; on needs-approval, open a DM with Diana (`conversations.open`) and post the approval card with buttons (`action_id` approve/reject, `value` = run id).
- **New `POST /bot/slack/interactive`**: Slack sends `application/x-www-form-urlencoded` with a `payload` JSON field; verify the same HMAC signature; handle `block_actions` → update run state, `chat.update` Diana's card, post the outcome to the origin channel/thread, write audit events. Idempotent: a second click on an already-decided run updates nothing and re-renders the decided card.
- Requester display name resolved via `users.info` (needs `users:read`); falls back to `<@id>` mention.
- Config: `SLACK_REFUND_APPROVER_ID` (Diana's Slack member ID) added to settings.

**One-time Slack app changes (user action, when creating test users):**
enable *Interactivity & Shortcuts* with request URL
`https://<api-host>/bot/slack/interactive`; add `im:write` and `users:read`
scopes; invite the bot and the test users to `#refunds`.

### 6. Audit API + web page

- `GET /runs` (list, newest first) and `GET /runs/{run_id}` (run + events) — same auth as other user-facing endpoints.
- **Runs** page in the existing React app (`web/`): list of refund runs (id, status pill, requester, amount, time) → detail view with the audit table (Time, Step, Detail, Who), styled consistently with existing pages. Nav entry "Runs".

## Error handling

- Order not found in retrieval → bot replies it can't find the order, run marked `completed` with a "not found" audit event.
- LLM failure / unparseable JSON → graceful error reply in channel; run marked with error event.
- `SLACK_REFUND_APPROVER_ID` unset → channel still gets the "needs approval" card with a note that no approver is configured; run stays `pending_approval`.
- DM delivery failure (e.g. missing `im:write`) → logged, channel notified that routing failed.
- Interactivity endpoint rejects invalid signatures (403) and unknown run ids (logged no-op).

## Testing

- **pytest**: workflow engine with mocked LLM/retrieval (both branches + malformed JSON), run store round-trip, interactivity endpoint (approve, reject, idempotent re-click, bad signature → 403), Slack card builders.
- **Live local e2e**: API running locally with real AI Search + LLM; simulated Slack event payloads signed with the real HMAC scheme; Slack HTTP API (`chat.postMessage`, `conversations.open`, `chat.update`, `users.info`) mocked at the HTTP layer; verify the full needs-approval flow (incl. button click) and the auto-approve flow; verify `GET /runs/{id}` audit trail matches the prototype's six steps.

## Out of scope

- Teams surface for the approval (prototype used Teams; this use case is Slack-only by request).
- The animated live "Run" progress view from the prototype (audit page only).
- Real payment/refund integration — issuance is a mocked terminal step.
- Per-user Slack→Entra identity mapping (bot keeps its org-wide identity for retrieval; requester identity is display-level).
- Org-chart-based approver resolution (config-based for the demo).
