from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.identity import User
from app.domain.workflow import RefundDecision
from app.workflows.engine import RefundEngineError
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore

_OVER_LIMIT = RefundDecision(
    found=True, order_id="48213", customer="Priya Sharma", amount_usd=1200,
    order_age_days=45, policy_limit_usd=500, policy_limit_days=30,
    auto_approve=False, reasoning="Over the $500 / 30 day auto-approve limit.",
)
_WITHIN = _OVER_LIMIT.model_copy(update={
    "order_id": "48190", "customer": "Marcus Lee", "amount_usd": 89.0,
    "order_age_days": 12, "auto_approve": True,
    "reasoning": "Within the $500 / 30 day auto-approve limit.",
})


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@substrateos",
                display_name="Bot", group_ids={"t-test:everyone"})


def _flow(decision=None, error=False):
    engine = AsyncMock()
    if error:
        engine.evaluate.side_effect = RefundEngineError("boom")
    else:
        engine.evaluate.return_value = decision
    store = RunStore(client=None, force_memory=True)
    return RefundFlow(engine=engine, store=store), store


def _slack_recorder():
    """Patchable fake slack_call that records (method, payload) and returns canned bodies."""
    calls: list[tuple[str, dict]] = []

    async def fake(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            uid = payload.get("user")
            names = {"U_TOM": "Tom Reyes", "U_DIANA": "Diana Foster"}
            return {"ok": True, "user": {"real_name": names.get(uid, "Someone"),
                                         "profile": {"display_name": ""}}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "111.222", "channel": payload["channel"]}
        return {"ok": True}

    return calls, fake


@pytest.mark.asyncio
async def test_needs_approval_path(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_APPROVER_ID", "U_DIANA")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund $1,200 order 48213", channel="C_REFUNDS",
                                  thread_ts="100.1", requester_slack_id="U_TOM", user=_user())
    runs = await store.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "pending_approval"
    assert run.dm_channel == "D_DIANA"
    assert run.dm_ts == "111.222"
    methods = [m for m, _ in calls]
    # ack + decision card to channel, users.info x2, conversations.open, DM card
    assert methods.count("chat.postMessage") == 3
    assert "conversations.open" in methods
    events = await store.list_events(run.id)
    steps = [e.step for e in events]
    assert steps == ["Request received", "Facts gathered", "Rule evaluated", "Routed for approval"]


@pytest.mark.asyncio
async def test_auto_approve_path(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_WITHIN)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund $89 order 48190", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "completed"
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Facts gathered", "Rule evaluated",
                     "Auto-approved", "Refund issued"]
    assert not any(m == "conversations.open" for m, _ in calls)


@pytest.mark.asyncio
async def test_engine_error_marks_run(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(error=True)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "error"


@pytest.mark.asyncio
async def test_order_not_found(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    nf = RefundDecision(found=False, reasoning="No order matching #99999 in the context.")
    flow, store = _flow(decision=nf)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 99999", channel="C", thread_ts=None,
                                  requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "completed"
    assert "Order not found" in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_no_approver_configured_still_posts_card(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_REFUND_APPROVER_ID", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"
    assert run.dm_channel is None
    assert not any(m == "conversations.open" for m, _ in calls)


@pytest.mark.asyncio
async def test_handle_action_approve(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                             channel="C_REFUNDS", thread_ts="100.1")
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    run.dm_channel, run.dm_ts = "D_DIANA", "111.222"
    await store.save(run)
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    }
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
    loaded = await store.get(run.id)
    assert loaded.status == "completed"
    assert loaded.approver_name == "Diana Foster"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Approved" in steps and "Refund issued" in steps
    methods = [m for m, _ in calls]
    assert "chat.update" in methods            # DM card replaced
    assert "chat.postMessage" in methods       # outcome to origin channel


@pytest.mark.asyncio
async def test_handle_action_reject(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C", thread_ts=None)
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    await store.save(run)
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": "refund_reject", "value": run.id}],
    }
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
    loaded = await store.get(run.id)
    assert loaded.status == "rejected"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Rejected" in steps and "Refund issued" not in steps


@pytest.mark.asyncio
async def test_handle_action_idempotent_second_click(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C", thread_ts=None)
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    run.dm_channel, run.dm_ts = "D_DIANA", "111.222"
    await store.save(run)
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    }
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
        n_events = len(await store.list_events(run.id))
        await flow.handle_action(payload)  # second click
    assert len(await store.list_events(run.id)) == n_events  # no duplicate audit entries


@pytest.mark.asyncio
async def test_handle_action_unknown_run_is_noop(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    payload = {"type": "block_actions", "user": {"id": "U_DIANA"},
               "actions": [{"action_id": "refund_approve", "value": "RB-0000"}]}
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)  # must not raise
    assert calls == []
