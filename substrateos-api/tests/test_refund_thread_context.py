"""Hand-off thread context: an agent's @mention inside a customer hand-off
thread reuses that run's already-fetched order facts instead of re-extracting
an order from the agent's message text — so "can we refund this" works.

Two seams under test:
- RunStore.find_handoff_run(channel, thread_ts) — locate the routed run whose
  support-channel card anchors the thread.
- RefundFlow.handle_request — prefer the linked run's facts over the engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.approvals.service import ApprovalService
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.connectors.act.stripe_mock import StripeRefundConnector
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.policy import Condition, Policy
from app.domain.workflow import RefundDecision, RefundFacts
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore

_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")
_DIANA = DirectoryUser(email="diana@x", slack_id="U_DIANA", display_name="Diana Foster",
                       groups=["Managers"], role="manager")

_REFUNDS_CHANNEL = "C_REFUNDS"
_CARD_TS = "100.1"


class _Directory:
    def __init__(self, *records: DirectoryUser) -> None:
        self._by_email = {r.email: r for r in records}
        self._by_slack = {r.slack_id: r for r in records if r.slack_id}

    async def resolve(self, email):  # noqa: ANN001
        return self._by_email.get((email or "").lower())

    async def get_by_slack_id(self, slack_id):  # noqa: ANN001
        return self._by_slack.get(slack_id)


def _user() -> User:
    return User(user_id="bot", tenant_id="t", email="bot@substrateos",
                display_name="Bot", group_ids=set())


async def _noop_slack(token, method, payload):
    if method == "users.info":
        people = {"U_TOM": ("Tom Reyes", "tom@x"), "U_DIANA": ("Diana Foster", "diana@x")}
        name, email = people.get(payload.get("user"), ("Someone", ""))
        return {"ok": True, "user": {"real_name": name,
                                     "profile": {"display_name": "", "email": email}}}
    if method == "conversations.open":
        return {"ok": True, "channel": {"id": "D_APPROVER"}}
    return {"ok": True, "ts": "1", "channel": payload.get("channel")}


def _decision(order_id: str = "48213") -> RefundDecision:
    return RefundDecision(found=True, order_id=order_id, customer="Priya Sharma",
                          customer_email="priya@x", amount_usd=1200,
                          order_age_days=47, auto_approve=False, reasoning="r")


async def _routed_run(store: RunStore, *, order_id: str = "48213",
                      status: str = "routed_to_support", kind: str = "refund",
                      handoff_channel: str | None = _REFUNDS_CHANNEL,
                      handoff_ts: str | None = _CARD_TS):
    run = await store.create(requester_name="Priya Sharma", requester_slack_id="U_PRIYA",
                             channel="D_PRIYA", thread_ts="50.1", kind=kind)
    run.decision = _decision(order_id)
    run.status = status
    run.handoff_channel = handoff_channel
    run.handoff_ts = handoff_ts
    await store.save(run)
    return run


# ── RunStore.find_handoff_run ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finds_run_by_handoff_thread():
    store = RunStore(client=None, force_memory=True)
    run = await _routed_run(store)
    found = await store.find_handoff_run(_REFUNDS_CHANNEL, _CARD_TS)
    assert found is not None and found.id == run.id


@pytest.mark.asyncio
async def test_latest_handoff_match_wins():
    store = RunStore(client=None, force_memory=True)
    await _routed_run(store)
    newer = await _routed_run(store)
    assert (await store.find_handoff_run(_REFUNDS_CHANNEL, _CARD_TS)).id == newer.id


@pytest.mark.asyncio
async def test_handoff_lookup_filters():
    store = RunStore(client=None, force_memory=True)
    await _routed_run(store, status="completed")              # already resolved
    await _routed_run(store, kind="approval")                 # different playbook
    await _routed_run(store, handoff_ts="999.9")              # different thread
    assert await store.find_handoff_run(_REFUNDS_CHANNEL, _CARD_TS) is None
    assert await store.find_handoff_run(None, _CARD_TS) is None
    assert await store.find_handoff_run(_REFUNDS_CHANNEL, None) is None


# ── RefundFlow: reuse over re-extraction ─────────────────────────────────────

_POLICY = Policy(
    id="refund.allow_small", version=1, owner="x",
    all=[Condition(fact="amount_usd", op="<=", value=500)],
    on_pass="allow", on_fail="deny", on_missing_data="stop",
)


def _flow(monkeypatch, *, engine_facts: RefundFacts | None = None):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    engine = AsyncMock()
    engine.evaluate.return_value = engine_facts or RefundFacts(
        found=False, reasoning="no order id in the request")

    class _PolicyStore:
        def load(self, policy_id):  # noqa: ANN001
            return _POLICY

    store = RunStore(client=None, force_memory=True)
    audit = AuditLog(client=None, force_memory=True)
    approvals = ApprovalService(store=ApprovalStore(client=None, force_memory=True),
                                audit=audit)
    flow = RefundFlow(engine=engine, store=store, directory=_Directory(_TOM, _DIANA),
                      audit_log=audit, refund_connector=StripeRefundConnector(),
                      approval_service=approvals, policy_store=_PolicyStore())
    return flow, store, engine


@pytest.mark.asyncio
async def test_agent_mention_in_handoff_thread_reuses_order(monkeypatch):
    flow, store, engine = _flow(monkeypatch)
    linked = await _routed_run(store)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="can we refund this", channel=_REFUNDS_CHANNEL,
                                  thread_ts=_CARD_TS, requester_slack_id="U_TOM",
                                  user=_user())
    engine.evaluate.assert_not_awaited()
    runs = await store.list_runs(limit=10)
    agent_run = next(r for r in runs if r.id != linked.id)
    assert agent_run.decision is not None and agent_run.decision.order_id == "48213"
    steps = [e.step for e in await store.list_events(agent_run.id)]
    assert "Order reused from hand-off" in steps
    assert "Order not found" not in steps


@pytest.mark.asyncio
async def test_agent_mention_outside_handoff_thread_still_extracts(monkeypatch):
    flow, store, engine = _flow(monkeypatch)
    await _routed_run(store)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="can we refund this", channel=_REFUNDS_CHANNEL,
                                  thread_ts="777.7",  # some other thread
                                  requester_slack_id="U_TOM", user=_user())
    engine.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_handoff_run_without_facts_falls_back_to_engine(monkeypatch):
    flow, store, engine = _flow(monkeypatch)
    run = await _routed_run(store)
    run.decision = None  # customer lookup failed at routing time — card has no facts
    await store.save(run)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="can we refund this", channel=_REFUNDS_CHANNEL,
                                  thread_ts=_CARD_TS, requester_slack_id="U_TOM",
                                  user=_user())
    engine.evaluate.assert_awaited_once()
