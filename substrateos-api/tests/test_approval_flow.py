"""Tests for the generic request-approval playbook."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.approvals.service import ApprovalService
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.domain.identity import User
from app.domain.workflow import RefundRun
from app.workflows.approval import ApprovalFlow
from app.workflows.store import RunStore


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@x",
                display_name="Bot", group_ids={"t-test:everyone"})


class _People:
    def __init__(self, mgr): self._mgr = mgr
    async def manager_of(self, *, email, tenant_id): return self._mgr


_DIANA = {"user_id": "u_diana", "email": "diana@x", "display_name": "Diana Foster"}


def _slack_recorder():
    calls: list[tuple[str, dict]] = []

    async def fake(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            uid = payload.get("user")
            prof = {"U_TOM": ("Tom Reyes", "tom@x"),
                    "U_DIANA": ("Diana Foster", "diana@x")}.get(uid, ("Someone", ""))
            return {"ok": True, "user": {"real_name": prof[0],
                                         "profile": {"display_name": prof[0], "email": prof[1]}}}
        if method == "users.lookupByEmail":
            return {"ok": True, "user": {"id": "U_DIANA"}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "111.222", "channel": payload["channel"]}
        return {"ok": True}

    return calls, fake


def _flow():
    store = RunStore(client=None, force_memory=True)
    return ApprovalFlow(store=store, people=_People(_DIANA)), store


# ── manager resolution path ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routes_to_resolved_manager(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow()
    calls, fake = _slack_recorder()
    with patch("app.workflows.approval.slack_call", new=fake), \
            patch("app.workflows.approval.slack_get", new=fake):
        await flow.handle_request(text="send this discount exception to my manager",
                                  channel="C_DEALS", thread_ts="9.9",
                                  requester_slack_id="U_TOM", user=_user())
    runs = await store.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.kind == "approval"
    assert run.status == "pending_approval"
    assert run.approver_name == "Diana Foster"
    assert run.approver_source == "manager"
    assert run.dm_channel == "D_DIANA" and run.dm_ts == "111.222"
    methods = [m for m, _ in calls]
    assert "users.lookupByEmail" in methods and "conversations.open" in methods
    # channel ack + DM card
    assert methods.count("chat.postMessage") == 2
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Approver resolved", "Routed for approval"]


@pytest.mark.asyncio
async def test_no_manager_means_no_fallback(monkeypatch):
    """No env-var fallback approver exists anymore — manager or stop."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = RunStore(client=None, force_memory=True)
    flow = ApprovalFlow(store=store, people=_People(None))  # no manager
    calls, fake = _slack_recorder()
    with patch("app.workflows.approval.slack_call", new=fake), \
            patch("app.workflows.approval.slack_get", new=fake):
        await flow.handle_request(text="get this signed off", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "error"
    assert run.approver_source is None
    assert "conversations.open" not in [m for m, _ in calls]


@pytest.mark.asyncio
async def test_no_approver_asks_requester(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = RunStore(client=None, force_memory=True)
    flow = ApprovalFlow(store=store, people=_People(None))
    calls, fake = _slack_recorder()
    with patch("app.workflows.approval.slack_call", new=fake), \
            patch("app.workflows.approval.slack_get", new=fake):
        await flow.handle_request(text="please approve", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "error"
    assert "conversations.open" not in [m for m, _ in calls]


# ── decision (button click) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_updates_run_and_notifies(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = RunStore(client=None, force_memory=True)
    flow = ApprovalFlow(store=store, people=_People(_DIANA))
    run: RefundRun = await store.create(
        requester_name="Tom Reyes", requester_slack_id="U_TOM", channel="C_DEALS",
        thread_ts="9.9", kind="approval", request_text="discount exception",
    )
    run.status = "pending_approval"
    run.dm_channel = "D_DIANA"
    run.dm_ts = "111.222"
    await store.save(run)
    _calls, fake = _slack_recorder()
    payload = {"type": "block_actions", "user": {"id": "U_DIANA", "name": "diana"},
               "actions": [{"action_id": "approval_approve", "value": run.id}],
               "container": {"channel_id": "D_DIANA", "message_ts": "111.222"}}
    with patch("app.workflows.approval.slack_call", new=fake), \
            patch("app.workflows.approval.slack_get", new=fake):
        await flow.handle_action(payload)
    updated = await store.get(run.id)
    assert updated.status == "approved"
    assert updated.approver_name == "Diana Foster"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Approved" in steps
    methods = [m for m, _ in _calls]
    assert "chat.update" in methods and "chat.postMessage" in methods  # DM updated + channel notified


@pytest.mark.asyncio
async def test_ignores_non_approval_action():
    store = RunStore(client=None, force_memory=True)
    flow = ApprovalFlow(store=store, people=None)
    # a refund action id must not be handled here
    await flow.handle_action({"type": "block_actions",
                              "actions": [{"action_id": "refund_approve", "value": "x"}]})
    assert await store.list_runs() == []


# ── governed resolution (ApprovalService + AuditLog) ─────────────────────────

@pytest.mark.asyncio
async def test_governed_approval_service_and_audit(monkeypatch):
    """After handle_request: approval_id set + pending w/ required_role==manager.
    After the routed approver's approve click: pending is 'approved' and
    AuditLog.query contains a human-actor event with decision=='approve'."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SUBSTRATEOS_TENANT_ID", "t-test")
    from app.config import get_settings
    get_settings.cache_clear()

    store = RunStore(client=None, force_memory=True)
    audit = AuditLog(force_memory=True)
    approval_service = ApprovalService(
        store=ApprovalStore(force_memory=True), audit=audit
    )
    flow = ApprovalFlow(
        store=store,
        people=_People(_DIANA),
        approval_service=approval_service,
        audit_log=audit,
    )

    calls, fake = _slack_recorder()
    with patch("app.workflows.approval.slack_call", new=fake), \
            patch("app.workflows.approval.slack_get", new=fake):
        await flow.handle_request(
            text="please approve this expense",
            channel="C_DEALS", thread_ts="9.9",
            requester_slack_id="U_TOM", user=_user(),
        )

    runs = await store.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "pending_approval"
    # approval_id must be set
    assert run.approval_id is not None
    # the pending approval must exist with required_role == "manager"
    pending = await approval_service.get_pending(run.approval_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.required_role == "manager"

    # now simulate the routed approver clicking Approve
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "actions": [{"action_id": "approval_approve", "value": run.id}],
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
    }
    with patch("app.workflows.approval.slack_call", new=fake), \
            patch("app.workflows.approval.slack_get", new=fake):
        await flow.handle_action(payload)

    updated = await store.get(run.id)
    assert updated.status == "approved"

    # pending must now be resolved
    pending_after = await approval_service.get_pending(run.approval_id)
    assert pending_after.status == "approved"

    # AuditLog must have a human-actor event with decision == "approve"
    events = await audit.query(run.id)
    human_approve = [
        e for e in events
        if e.actor.type == "human" and e.decision == "approve"
    ]
    assert human_approve, f"No human approve audit event found; events={events}"
