# Refund Outcome Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a manager decides a refund, the outcome fans out to every surface it touched — agent @-mentioned, customer answered in their original thread (or DM), the support-channel hand-off card marked resolved — with both runs' audit trails recording the relay.

**Architecture:** The customer's `routed_to_support` run is found by `order_id` (`RunStore.find_routed_run`); `RefundDecision` gains `customer_email` for the DM fallback; `RefundRun` gains `handoff_channel`/`handoff_ts` so the support-channel card can be resolved. All relay work lives in one fail-soft `RefundFlow._notify_customer` called after the decision is recorded.

**Tech Stack:** FastAPI · Python 3.12 · uv · pytest (existing fakes patterns) · Slack Web API.

**Spec:** `docs/superpowers/specs/2026-06-07-refund-outcome-notifications-design.md` — read first.
**Branch:** `feat/refund-outcome-notify` (exists, spec committed).
**Tests:** from `substrateos-api/` via `uv run pytest <files> -q`. NEVER the bare `tests/` dir without `-m "not integration"`.

**File map:**

| Action | Path | Responsibility |
|---|---|---|
| Modify | `substrateos-api/app/domain/workflow.py` | +`customer_email` on decision, +handoff fields on run |
| Modify | `web/lib/runsApi.ts` | mirror both |
| Modify | `substrateos-api/app/workflows/engine.py` | prompt extracts `customer_email` |
| Modify | `substrateos-api/app/workflows/store.py` | `find_routed_run(order_id)` |
| Modify | `substrateos-api/app/bots/refund_cards.py` | `outcome_blocks` mention + `customer_outcome_blocks` |
| Modify | `substrateos-api/app/workflows/flow.py` | persist handoff ts; outcome fan-out |
| Tests | `tests/test_workflow_models.py`, `tests/test_refund_engine_requester.py`, `tests/test_run_store_finder.py` (new), `tests/test_refund_cards.py`, `tests/test_refund_flow.py` | |

Task order: 1 first (model fields), then 2–4 in any order (independent files), then 5, then 6.

---

### Task 1: Model fields + web mirror

**Files:**
- Modify: `substrateos-api/app/domain/workflow.py`
- Modify: `web/lib/runsApi.ts`
- Test: `substrateos-api/tests/test_workflow_models.py` (append)

- [ ] **Step 1: failing test** — append to `tests/test_workflow_models.py`:

```python
def test_outcome_notification_fields():
    from datetime import UTC, datetime

    from app.domain.workflow import RefundDecision, RefundRun

    d = RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                       customer_email="priya@x", auto_approve=False, reasoning="r")
    assert d.customer_email == "priya@x"
    assert RefundDecision(found=False, reasoning="r").customer_email is None

    now = datetime.now(UTC)
    run = RefundRun(id="RB-1", requester_name="Priya", status="routed_to_support",
                    handoff_channel="C_SUPPORT", handoff_ts="222.333",
                    created_at=now, updated_at=now)
    assert run.handoff_channel == "C_SUPPORT" and run.handoff_ts == "222.333"
    legacy = RefundRun(id="RB-2", requester_name="X", created_at=now, updated_at=now)
    assert legacy.handoff_channel is None and legacy.handoff_ts is None
```

- [ ] **Step 2:** `cd substrateos-api && uv run pytest tests/test_workflow_models.py -q` — new test FAILS (unexpected field).

- [ ] **Step 3: implement** — in `app/domain/workflow.py`:

In `RefundDecision`, directly under `customer: str | None = None`, add:

```python
    customer_email: str | None = None  # from the order record — powers the outcome DM fallback
```

In `RefundRun`, directly under `thread_ts: str | None = None`, add:

```python
    # support-channel hand-off card (customer routed runs) — resolved on outcome
    handoff_channel: str | None = None
    handoff_ts: str | None = None
```

- [ ] **Step 4:** `uv run pytest tests/test_workflow_models.py -q` — all pass.

- [ ] **Step 5: web mirror** — in `web/lib/runsApi.ts`:

`RefundDecision` type gains (after `customer: string | null;`):

```ts
  customer_email?: string | null;
```

`RunSummary` gains (after the `decision` line):

```ts
  handoff_channel?: string | null;
  handoff_ts?: string | null;
```

Run: `cd web && pnpm typecheck` — clean.

- [ ] **Step 6: Commit**

```bash
git add substrateos-api/app/domain/workflow.py substrateos-api/tests/test_workflow_models.py web/lib/runsApi.ts
git commit -m "feat(workflows): customer_email on decisions, hand-off card handles on runs"
```

---

### Task 2: Engine extracts `customer_email`

**Files:**
- Modify: `substrateos-api/app/workflows/engine.py` (`DECISION_PROMPT`)
- Test: `substrateos-api/tests/test_refund_engine_requester.py` (append)

- [ ] **Step 1: failing test** — append to `tests/test_refund_engine_requester.py`:

```python
@pytest.mark.asyncio
async def test_decision_carries_customer_email():
    retriever, llm = _Retriever(), _LLM()
    # the fake decision JSON gains the email, as the prompt now requests
    global _DECISION_JSON
    payload = json.loads(_DECISION_JSON)
    payload["customer_email"] = "priya@x"

    class _EmailLLM(_LLM):
        async def complete(self, *, messages, temperature, max_tokens):
            self.messages = messages
            return json.dumps(payload)

    engine = RefundEngine(retriever=retriever, llm=_EmailLLM())
    decision = await engine.evaluate("refund my order", user=_user(), requester=_PRIYA)
    assert decision.customer_email == "priya@x"


def test_decision_prompt_requests_customer_email():
    from app.workflows.engine import DECISION_PROMPT

    assert '"customer_email"' in DECISION_PROMPT
    assert "email" in DECISION_PROMPT.lower()
```

- [ ] **Step 2:** `uv run pytest tests/test_refund_engine_requester.py -q` — `test_decision_prompt_requests_customer_email` FAILS (the carries-email test may already pass since pydantic now has the field — that's fine; the prompt test is the gate).

- [ ] **Step 3: implement** — in `app/workflows/engine.py`, replace the JSON schema line of `DECISION_PROMPT`:

```python
    'Respond ONLY with valid JSON, no other text:\n'
    '{"found": true, "order_id": "...", "customer": "...", "customer_email": "...", '
    '"amount_usd": 0, '
    '"order_age_days": 0, "policy_limit_usd": 0, "policy_limit_days": 0, '
    '"auto_approve": true, "reasoning": "one sentence citing the policy"}\n'
    'Copy the customer\'s email address from the order record into customer_email '
    'when present, else use null. '
    'If the order cannot be found in the context documents, respond with '
    '{"found": false, "reasoning": "..."}.'
```

(Keep the leading instruction sentences of the prompt unchanged; only the schema/closing portion changes as shown.)

- [ ] **Step 4:** `uv run pytest tests/test_refund_engine_requester.py tests/test_refund_engine.py -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/engine.py substrateos-api/tests/test_refund_engine_requester.py
git commit -m "feat(refund): decision extracts the customer's email from the order record"
```

---

### Task 3: `RunStore.find_routed_run`

**Files:**
- Modify: `substrateos-api/app/workflows/store.py` (append method to `RunStore`)
- Test: `substrateos-api/tests/test_run_store_finder.py` (new)

- [ ] **Step 1: failing tests** — `tests/test_run_store_finder.py`:

```python
"""find_routed_run: links a decided refund back to the customer's hand-off run."""

from __future__ import annotations

import pytest

from app.domain.workflow import RefundDecision
from app.workflows.store import RunStore


def _decision(order_id: str) -> RefundDecision:
    return RefundDecision(found=True, order_id=order_id, customer="Priya Sharma",
                          amount_usd=1200, auto_approve=False, reasoning="r")


async def _routed(store, order_id: str, status: str = "routed_to_support",
                  kind: str = "refund"):
    run = await store.create(requester_name="Priya Sharma", requester_slack_id="U_PRIYA",
                             channel="D_PRIYA", thread_ts="50.1", kind=kind)
    run.decision = _decision(order_id)
    run.status = status
    await store.save(run)
    return run


@pytest.mark.asyncio
async def test_finds_matching_routed_run():
    store = RunStore(client=None, force_memory=True)
    run = await _routed(store, "48213")
    found = await store.find_routed_run("48213")
    assert found is not None and found.id == run.id


@pytest.mark.asyncio
async def test_latest_match_wins():
    store = RunStore(client=None, force_memory=True)
    await _routed(store, "48213")
    newer = await _routed(store, "48213")
    assert (await store.find_routed_run("48213")).id == newer.id


@pytest.mark.asyncio
async def test_filters_status_kind_order_and_none():
    store = RunStore(client=None, force_memory=True)
    await _routed(store, "48213", status="completed")        # already resolved
    await _routed(store, "99999")                            # different order
    await _routed(store, "48213", kind="approval")           # different kind
    assert (await store.find_routed_run("48213")) is None
    assert (await store.find_routed_run(None)) is None
    assert (await store.find_routed_run("48213")) is None
```

- [ ] **Step 2:** `uv run pytest tests/test_run_store_finder.py -q` — FAILS (`AttributeError: find_routed_run`).

- [ ] **Step 3: implement** — append to `RunStore` in `app/workflows/store.py`:

```python
    async def find_routed_run(self, order_id: str | None) -> RefundRun | None:
        """Most recent customer hand-off run for this order still awaiting an
        outcome. list_runs is newest-first, so the first match wins. Notification
        flips the run's status, so a second lookup finds nothing — natural
        double-notify protection."""
        if not order_id:
            return None
        for run in await self.list_runs(limit=100):
            if (run.kind == "refund" and run.status == "routed_to_support"
                    and run.decision is not None
                    and run.decision.order_id == order_id):
                return run
        return None
```

- [ ] **Step 4:** `uv run pytest tests/test_run_store_finder.py -q` — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/store.py substrateos-api/tests/test_run_store_finder.py
git commit -m "feat(workflows): find the customer's hand-off run by order id"
```

---

### Task 4: Cards — mention + customer outcome

**Files:**
- Modify: `substrateos-api/app/bots/refund_cards.py`
- Test: `substrateos-api/tests/test_refund_cards.py` (append)

- [ ] **Step 1: failing tests** — append to `tests/test_refund_cards.py`:

```python
def _decision_for_cards():
    from app.domain.workflow import RefundDecision
    return RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                          amount_usd=1200, order_age_days=45, policy_limit_usd=500,
                          policy_limit_days=30, auto_approve=False, reasoning="over limit")


def test_outcome_blocks_mentions_agent():
    from app.bots.refund_cards import outcome_blocks

    card = outcome_blocks(_decision_for_cards(), approved=True,
                          approver_name="Diana Foster", mention="<@U_TOM>")
    body = str(card["attachments"])
    assert "Hello <@U_TOM>" in body and "Diana Foster" in body

    plain = outcome_blocks(_decision_for_cards(), approved=False,
                           approver_name="Diana Foster")
    assert "Hello" not in str(plain["attachments"])  # no mention → old header


def test_customer_outcome_blocks_approved_and_rejected():
    from app.bots.refund_cards import customer_outcome_blocks

    ok = customer_outcome_blocks(_decision_for_cards(), approved=True)
    body = str(ok["attachments"])
    assert "Hello Priya" in body and "$1,200" in body and "#48213" in body
    assert "approved" in body and ok["attachments"][0]["color"] == "#2f8f5b"

    no = customer_outcome_blocks(_decision_for_cards(), approved=False)
    body = str(no["attachments"]).lower()
    assert "hello priya" in body and "refund policy" in body and "$500" in body
    # customer copy never leaks internal mechanics
    for banned in ("exception", "manager", "approv"):
        assert banned not in body
    assert no["attachments"][0]["color"] == "#c8546a"
```

- [ ] **Step 2:** `uv run pytest tests/test_refund_cards.py -q` — new tests FAIL.

- [ ] **Step 3: implement** — in `app/bots/refund_cards.py`:

Replace `outcome_blocks` with:

```python
def outcome_blocks(d: RefundDecision, *, approved: bool, approver_name: str,
                   mention: str | None = None) -> dict:
    verdict = "approved" if approved else "rejected"
    if approved:
        head = (f":white_check_mark: *Hello {mention} — refund {verdict} by {approver_name}*"
                if mention else f":white_check_mark: *Approved by {approver_name}*")
        body = f"Refund of {_usd(d.amount_usd)} issued to {d.customer} on order #{d.order_id}. Confirmation sent."
    else:
        head = (f":x: *Hello {mention} — refund {verdict} by {approver_name}*"
                if mention else f":x: *Rejected by {approver_name}*")
        body = f"The refund of {_usd(d.amount_usd)} on order #{d.order_id} was declined."
    return {"attachments": [_bar(_GREEN if approved else _RED, [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{head}\n{body}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded with the decision"}]},
    ])]}
```

Append:

```python
def customer_outcome_blocks(d: RefundDecision, *, approved: bool) -> dict:
    """Customer-facing outcome — policy facts only, never internal mechanics
    (no managers, approvals, or exceptions in the REJECTED copy)."""
    first = (d.customer or "there").split()[0]
    if approved:
        text = (f"Hello {first} — good news! Your refund of {_usd(d.amount_usd)} for "
                f"order #{d.order_id} has been approved and is being processed.")
        color = _GREEN
    else:
        text = (f"Hello {first} — we couldn't process your refund for order "
                f"#{d.order_id}: it falls outside our refund policy "
                f"({_usd(d.policy_limit_usd)} within {d.policy_limit_days} days). "
                "Please reach out to our support team if you have questions.")
        color = _RED
    return {"attachments": [_bar(color, [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ])]}
```

- [ ] **Step 4:** `uv run pytest tests/test_refund_cards.py -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/refund_cards.py substrateos-api/tests/test_refund_cards.py
git commit -m "feat(refund): agent mention on outcome card + customer-facing outcome card"
```

---

### Task 5: Flow — persist hand-off ts + outcome fan-out

**Files:**
- Modify: `substrateos-api/app/workflows/flow.py`
- Test: `substrateos-api/tests/test_refund_flow.py` (modify + append)

- [ ] **Step 1: failing tests.**

(a) In `tests/test_refund_flow.py`, in `_slack_recorder`'s fake, replace the `conversations.open` branch with a per-user mapping:

```python
        if method == "conversations.open":
            dm = {"U_DIANA": "D_DIANA", "U_PRIYA": "D_PRIYA_DM"}.get(
                payload.get("users"), "D_OTHER")
            return {"ok": True, "channel": {"id": dm}}
```

(b) In `test_customer_routes_to_support_with_prefetched_order`, add after the existing run assertions:

```python
    assert run.handoff_channel == "C_SUPPORT"
    assert run.handoff_ts == "111.222"
```

(c) Update the module fixtures: `_OVER_LIMIT` gains the email — change its construction to include `customer_email="priya@x"`:

```python
_OVER_LIMIT = RefundDecision(
    found=True, order_id="48213", customer="Priya Sharma", customer_email="priya@x",
    amount_usd=1200,
    order_age_days=45, policy_limit_usd=500, policy_limit_days=30,
    auto_approve=False, reasoning="Over the $500 / 30 day auto-approve limit.",
)
```

(d) Append a helper + four tests:

```python
async def _routed_customer_run(store, *, with_handoff: bool = True):
    """Priya's earlier hand-off run, awaiting an outcome."""
    run = await store.create(requester_name="Priya Sharma", requester_slack_id="U_PRIYA",
                             channel="D_PRIYA", thread_ts="50.1")
    run.decision = _OVER_LIMIT
    run.status = "routed_to_support"
    if with_handoff:
        run.handoff_channel, run.handoff_ts = "C_SUPPORT", "222.333"
    await store.save(run)
    return run


@pytest.mark.asyncio
async def test_approve_fans_out_to_agent_customer_and_handoff(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    linked = await _routed_customer_run(store)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_DIANA", name="diana"))
    posts = [p for m, p in calls if m == "chat.postMessage"]
    # 1. agent channel post mentions Tom
    agent_post = next(p for p in posts if p["channel"] == "C_REFUNDS")
    assert "<@U_TOM>" in str(agent_post)
    # 2. customer's original thread gets the good news
    cust_post = next(p for p in posts if p["channel"] == "D_PRIYA")
    assert cust_post.get("thread_ts") == "50.1"
    assert "good news" in str(cust_post) and "#48213" in str(cust_post)
    # 3. hand-off card in the support channel marked resolved
    handoff_post = next(p for p in posts if p["channel"] == "C_SUPPORT")
    assert handoff_post.get("thread_ts") == "222.333"
    assert "Resolved" in str(handoff_post) and "approved" in str(handoff_post)
    # 4. linked run closed + audited; deciding run audited
    linked2 = await store.get(linked.id)
    assert linked2.status == "completed"
    assert "Outcome relayed" in [e.step for e in await store.list_events(linked.id)]
    assert "Customer notified" in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_reject_customer_copy_has_no_internal_language(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    await _routed_customer_run(store)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_reject", run.id,
                                        user_id="U_DIANA", name="diana"))
    posts = [p for m, p in calls if m == "chat.postMessage"]
    cust = str(next(p for p in posts if p["channel"] == "D_PRIYA")).lower()
    assert "refund policy" in cust and "$500" in cust
    for banned in ("exception", "manager", "approv", "diana"):
        assert banned not in cust
    # the linked customer run is closed as rejected
    linked = next(r for r in await store.list_runs()
                  if r.requester_name == "Priya Sharma")
    assert linked.status == "rejected"


@pytest.mark.asyncio
async def test_dm_fallback_when_no_linked_run(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)   # directory has _PRIYA (slack U_PRIYA)
    run = await _pending_run(store)             # no routed run exists
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_DIANA", name="diana"))
    opened = [p for m, p in calls if m == "conversations.open"]
    assert {"users": "U_PRIYA"} in opened
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert any(p["channel"] == "D_PRIYA_DM" and "good news" in str(p) for p in posts)
    assert "Customer notified" in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_skip_when_unreachable_and_mention_fallback(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    no_email = _OVER_LIMIT.model_copy(update={"customer_email": None})
    flow, store = _flow(decision=no_email, directory=_Directory(_TOM, _DIANA))
    run = await store.create(requester_name="Tom Reyes", requester_slack_id=None,
                             channel="C_REFUNDS", thread_ts="100.1")
    run.decision = no_email
    run.status = "pending_approval"
    run.approver_slack_id = "U_DIANA"
    await store.save(run)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_DIANA", name="diana"))
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert not any(p["channel"].startswith("D_PRIYA") for p in posts)
    assert "Customer not reachable" in [e.step for e in await store.list_events(run.id)]
    # no requester_slack_id → plain-name header, no broken mention
    agent_post = str(next(p for p in posts if p["channel"] == "C_REFUNDS"))
    assert "<@" not in agent_post.replace("<@U_DIANA>", "")  # no agent mention
    assert "Tom Reyes" in agent_post
```

- [ ] **Step 2:** `uv run pytest tests/test_refund_flow.py -q` — new tests FAIL.

- [ ] **Step 3: implement** — in `app/workflows/flow.py`:

(a) Import the new card builder (extend the refund_cards import): add `customer_outcome_blocks`.

(b) In `_route_to_support`, the success branch persists the hand-off handles — replace:

```python
        run.status = "routed_to_support"
        await self._store.save(run)
```

with:

```python
        run.status = "routed_to_support"
        run.handoff_channel = support_channel
        run.handoff_ts = posted.get("ts")
        await self._store.save(run)
```

(c) In `handle_action`, replace the final channel-outcome block:

```python
        if run.channel:
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Refund {'approved' if approved else 'rejected'} by {approver_name}",
                             card=outcome_blocks(d, approved=approved, approver_name=approver_name))
```

with:

```python
        if run.channel:
            mention = (f"<@{run.requester_slack_id}>" if run.requester_slack_id else None)
            label = mention or run.requester_name
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Hello {label} — refund {'approved' if approved else 'rejected'} by {approver_name}",
                             card=outcome_blocks(d, approved=approved,
                                                 approver_name=approver_name, mention=label))
        await self._notify_customer(token, run, approved=approved,
                                    approver_name=approver_name)
```

(d) Add the relay method (after `_route_to_support`):

```python
    async def _notify_customer(self, token: str, run, *, approved: bool,
                               approver_name: str) -> None:
        """Relay the outcome to the customer (their original thread, else a DM via
        the directory) and mark the support-channel hand-off card resolved.
        Fail-soft: the relay must never break the recorded decision."""
        d = run.decision
        if d is None or not d.order_id:
            return
        try:
            linked = await self._store.find_routed_run(d.order_id)
            notified_where: str | None = None
            if linked is not None and linked.channel:
                posted = await slack_call(token, "chat.postMessage", {
                    "channel": linked.channel, "thread_ts": linked.thread_ts,
                    "text": f"Your refund was {'approved' if approved else 'declined'}",
                    **customer_outcome_blocks(d, approved=approved),
                })
                if posted:
                    notified_where = "their thread"
                    linked.status = "completed" if approved else "rejected"
                    await self._store.save(linked)
                    await self._store.add_event(
                        linked.id, step="Outcome relayed",
                        detail=(f"{'Approved' if approved else 'Rejected'} by "
                                f"{approver_name} — customer notified"),
                        actor="SubstrateOS")
            elif d.customer_email:
                record = await self._directory.resolve(d.customer_email)
                if record is not None and record.slack_id:
                    opened = await slack_call(token, "conversations.open",
                                              {"users": record.slack_id})
                    dm = ((opened or {}).get("channel") or {}).get("id")
                    if dm:
                        posted = await slack_call(token, "chat.postMessage", {
                            "channel": dm,
                            "text": f"Your refund was {'approved' if approved else 'declined'}",
                            **customer_outcome_blocks(d, approved=approved),
                        })
                        if posted:
                            notified_where = "a DM"
            await self._store.add_event(
                run.id,
                step="Customer notified" if notified_where else "Customer not reachable",
                detail=(f"Outcome sent to {d.customer} in {notified_where}" if notified_where
                        else f"No conversation or directory match for {d.customer or 'the customer'}"),
                actor="SubstrateOS")
            if linked is not None and linked.handoff_channel and linked.handoff_ts:
                mark = "✅" if approved else "✕"
                suffix = "customer notified" if notified_where else "customer not reachable"
                await slack_call(token, "chat.postMessage", {
                    "channel": linked.handoff_channel, "thread_ts": linked.handoff_ts,
                    "text": (f"{mark} Resolved — "
                             f"{'approved' if approved else 'rejected'} by {approver_name}, {suffix}"),
                })
        except Exception:  # noqa: BLE001 — relay must never break the decision
            logger.exception("customer outcome relay failed for run %s", run.id)
```

- [ ] **Step 4:** `uv run pytest tests/test_refund_flow.py tests/test_refund_cards.py -q` — all pass. (If the idempotent-second-click test counts events, note the first click now adds relay events — the second click still adds none, so it stays valid.)

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/flow.py substrateos-api/tests/test_refund_flow.py
git commit -m "feat(refund): outcome fan-out — agent mention, customer reply, hand-off card resolved"
```

---

### Task 6: Full verification + docs

- [ ] **Step 1:** `cd substrateos-api && uv run pytest tests/ -q -m "not integration" -p no:cacheprovider` — all pass; paste the summary line.

- [ ] **Step 2:** `cd web && pnpm typecheck` — clean (lint is known-broken repo-wide, skip).

- [ ] **Step 3:** `mockups/architecture.html` (Master Deck palette, reuse classes): in the refund playbook description, extend the Record/outcome line with "decision fans out: agent @-mentioned, customer answered in their thread (DM fallback), hand-off card resolved — both runs audited". Open the file to eyeball.

- [ ] **Step 4: Commit**

```bash
git add mockups/architecture.html
git commit -m "docs(architecture): refund outcome fan-out"
```

---

## Post-merge actions (final report)

1. Deploy `substrateos-api` (explicit approval).
2. Demo: Priya asks → hand-off card → Tom raises → Diane approves → watch all
   four surfaces update; then a reject round for the customer-friendly copy.
