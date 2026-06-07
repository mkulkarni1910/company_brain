from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.directory import DirectoryUser
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

_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")
_DIANA = DirectoryUser(email="diana@x", slack_id="U_DIANA", display_name="Diana Foster",
                       groups=["Managers"], role="manager")
_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")


class _Directory:
    """In-memory stand-in for DirectoryService."""

    def __init__(self, *records: DirectoryUser) -> None:
        self._by_email = {r.email: r for r in records}
        self._by_slack = {r.slack_id: r for r in records if r.slack_id}

    async def resolve(self, email):  # noqa: ANN001
        return self._by_email.get((email or "").lower())

    async def get_by_slack_id(self, slack_id):  # noqa: ANN001
        return self._by_slack.get(slack_id)


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@substrateos",
                display_name="Bot", group_ids={"t-test:everyone"})


def _flow(decision=None, error=False, directory=None):
    engine = AsyncMock()
    if error:
        engine.evaluate.side_effect = RefundEngineError("boom")
    else:
        engine.evaluate.return_value = decision
    store = RunStore(client=None, force_memory=True)
    directory = directory if directory is not None else _Directory(_TOM, _DIANA, _PRIYA)
    return RefundFlow(engine=engine, store=store, directory=directory), store


def _slack_recorder():
    """Patchable fake slack_call that records (method, payload) and returns canned bodies."""
    calls: list[tuple[str, dict]] = []

    async def fake(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            uid = payload.get("user")
            people = {"U_TOM": ("Tom Reyes", "tom@x"),
                      "U_DIANA": ("Diana Foster", "diana@x"),
                      "U_PRIYA": ("Priya Sharma", "priya@x")}
            name, email = people.get(uid, ("Someone", ""))
            return {"ok": True, "user": {"real_name": name,
                                         "profile": {"display_name": "", "email": email}}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "111.222", "channel": payload["channel"]}
        return {"ok": True}

    return calls, fake


# ── agent path: routes to the requester's Entra manager ───────────────────────

@pytest.mark.asyncio
async def test_needs_approval_routes_to_managers_dm(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund $1,200 order 48213", channel="C_REFUNDS",
                                  thread_ts="100.1", requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"
    assert run.approver_name == "Diana Foster"
    assert run.approver_slack_id == "U_DIANA"
    assert run.dm_channel == "D_DIANA" and run.dm_ts == "111.222"
    opened = [p for m, p in calls if m == "conversations.open"]
    assert opened == [{"users": "U_DIANA"}]
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Identity checked", "Facts gathered",
                     "Rule evaluated", "Routed for approval"]
    routed = [e for e in await store.list_events(run.id) if e.step == "Routed for approval"][0]
    assert "Tom Reyes's manager" in routed.detail and "Managers" in routed.detail


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
    assert steps == ["Request received", "Identity checked", "Facts gathered",
                     "Rule evaluated", "Auto-approved", "Refund issued"]
    assert not any(m == "conversations.open" for m, _ in calls)


# ── stop-the-run: no usable manager ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_without_manager_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    orphan = _TOM.model_copy(update={"manager_email": None})
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory(orphan, _DIANA))
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 48213", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    assert "No eligible approver" in [e.step for e in await store.list_events(run.id)]
    assert not any(m == "conversations.open" for m, _ in calls)


@pytest.mark.asyncio
async def test_manager_not_in_managers_group_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    demoted = _DIANA.model_copy(update={"role": "agent", "groups": ["Support Agent"]})
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory(_TOM, demoted))
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 48213", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    detail = [e for e in await store.list_events(run.id)
              if e.step == "No eligible approver"][0].detail
    assert "Managers" in detail


@pytest.mark.asyncio
async def test_manager_without_slack_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    no_slack = _DIANA.model_copy(update={"slack_id": None})
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory(_TOM, no_slack))
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 48213", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    assert (await store.list_runs())[0].status == "needs_attention"


# ── customer path ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_routes_to_support_with_prefetched_order(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_CHANNEL_ID", "C_SUPPORT")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="I want a refund for order 48213", channel="D_PRIYA",
                                  thread_ts=None, requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "routed_to_support"
    assert run.decision is not None and run.decision.order_id == "48213"
    # engine ran scoped to the customer
    kwargs = flow._engine.evaluate.await_args.kwargs
    assert kwargs["requester"].email == "priya@x"
    posts = [p for m, p in calls if m == "chat.postMessage"]
    support_post = next(p for p in posts if p["channel"] == "C_SUPPORT")
    assert "48213" in str(support_post)              # facts on the card
    assert any(p["channel"] == "D_PRIYA" for p in posts)
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Order fetched" in steps and "Routed to support" in steps


@pytest.mark.asyncio
async def test_customer_routing_survives_engine_error(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_CHANNEL_ID", "C_SUPPORT")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(error=True)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund please", channel="D_PRIYA", thread_ts=None,
                                  requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "routed_to_support"   # lookup failure never blocks routing
    assert run.decision is None
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert any(p["channel"] == "C_SUPPORT" for p in posts)  # bare card still posted
    assert "Order fetched" not in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_customer_without_channel_config_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_REFUND_CHANNEL_ID", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund please", channel="D_PRIYA", thread_ts=None,
                                  requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    assert "No support channel" in [e.step for e in await store.list_events(run.id)]


# ── identity unknown ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_identity_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory())  # empty directory
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    assert "Identity unknown" in [e.step for e in await store.list_events(run.id)]
    flow._engine.evaluate.assert_not_called()


# ── engine outcomes (agent identity established) ───────────────────────────────

@pytest.mark.asyncio
async def test_engine_error_marks_run(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(error=True)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    assert (await store.list_runs())[0].status == "error"


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
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "completed"
    assert "Order not found" in [e.step for e in await store.list_events(run.id)]


# ── button clicks: manager-only enforcement ────────────────────────────────────

async def _pending_run(store):
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                             channel="C_REFUNDS", thread_ts="100.1")
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    run.approver_slack_id = "U_DIANA"
    run.dm_channel, run.dm_ts = "D_DIANA", "111.222"
    await store.save(run)
    return run


def _click(action_id: str, run_id: str, *, user_id: str, name: str) -> dict:
    return {
        "type": "block_actions",
        "user": {"id": user_id, "name": name},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": action_id, "value": run_id}],
    }


@pytest.mark.asyncio
async def test_routed_manager_can_approve(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_DIANA", name="diana"))
    loaded = await store.get(run.id)
    assert loaded.status == "completed"
    assert loaded.approver_name == "Diana Foster"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Approved" in steps and "Refund issued" in steps


@pytest.mark.asyncio
async def test_agent_click_is_denied(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_TOM", name="tom"))
    loaded = await store.get(run.id)
    assert loaded.status == "pending_approval"          # untouched
    assert "Approval denied" in [e.step for e in await store.list_events(run.id)]
    assert any(m == "chat.postEphemeral" for m, _ in calls)


@pytest.mark.asyncio
async def test_unknown_clicker_is_denied(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_reject", run.id,
                                        user_id="U_STRANGER", name="who"))
    assert (await store.get(run.id)).status == "pending_approval"


@pytest.mark.asyncio
async def test_legacy_run_without_routed_approver_is_denied(monkeypatch):
    """A pending run with no recorded approver_slack_id is hard-denied — even
    for a legitimate manager — rather than open to any manager."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    run.approver_slack_id = None  # legacy: created before directory routing
    await store.save(run)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_DIANA", name="diana"))
    assert (await store.get(run.id)).status == "pending_approval"
    assert "Approval denied" in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_handle_action_reject(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_reject", run.id,
                                        user_id="U_DIANA", name="diana"))
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
    run = await _pending_run(store)
    payload = _click("refund_approve", run.id, user_id="U_DIANA", name="diana")
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
        n_events = len(await store.list_events(run.id))
        await flow.handle_action(payload)  # second click
    assert len(await store.list_events(run.id)) == n_events


@pytest.mark.asyncio
async def test_handle_action_unknown_run_is_noop(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", "RB-0000",
                                        user_id="U_DIANA", name="diana"))
    assert calls == []
