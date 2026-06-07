"""Workstream 4 — seam-extraction integration.

The flow now CALLS services: PolicyEngine (verdict), AuditLog (typed provenance),
and the Stripe connector (the act). This proves the typed, identity-stamped trail
and the connector receipt — not just the human-readable run timeline.

All tests drive the flow through the NEW directory-aware contract: a fake
DirectoryService whose resolve(email) returns a real DirectoryUser (role="agent",
manager set) and get_by_slack_id(id) returns the manager record.
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
from app.domain.workflow import RefundFacts
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore

# ── shared directory personas ────────────────────────────────────────────────

_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")
_DIANA = DirectoryUser(email="diana@x", slack_id="U_DIANA", display_name="Diana Foster",
                       groups=["Managers"], role="manager")


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
    return User(user_id="bot", tenant_id="t", email="bot@substrateos",
                display_name="Bot", group_ids=set())


async def _noop_slack(token, method, payload):
    """Fake slack_call: handles users.info + conversations.open; returns canned ts."""
    if method == "users.info":
        uid = payload.get("user")
        people = {"U_TOM": ("Tom Reyes", "tom@x"), "U_DIANA": ("Diana Foster", "diana@x")}
        name, email = people.get(uid, ("Someone", ""))
        return {"ok": True, "user": {"real_name": name,
                                     "profile": {"display_name": "", "email": email}}}
    if method == "conversations.open":
        return {"ok": True, "channel": {"id": "D_APPROVER"}}
    return {"ok": True, "ts": "1", "channel": payload.get("channel")}


class _FakePolicyStore:
    """Returns a fixed policy, to exercise deny/stop dispatch the real file can't."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    def load(self, policy_id: str) -> Policy:
        return self._policy


def _facts_over() -> RefundFacts:
    return RefundFacts(found=True, order_id="48213", customer="Priya Sharma",
                       amount_usd=1200, order_age_days=45, reasoning="over")


def _flow_with(policy, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    engine = AsyncMock()
    engine.evaluate.return_value = _facts_over()
    store = RunStore(client=None, force_memory=True)
    audit = AuditLog(client=None, force_memory=True)
    approvals = ApprovalService(store=ApprovalStore(client=None, force_memory=True), audit=audit)
    directory = _Directory(_TOM, _DIANA)
    flow = RefundFlow(engine=engine, store=store, directory=directory, audit_log=audit,
                      refund_connector=StripeRefundConnector(),
                      approval_service=approvals, policy_store=_FakePolicyStore(policy))
    return flow, store, audit, approvals


@pytest.mark.asyncio
async def test_deny_refuses_without_routing_to_approval(monkeypatch):
    deny_policy = Policy(
        id="refund.deny", version=1, owner="x",
        all=[Condition(fact="amount_usd", op="<=", value=500)],
        on_pass="allow", on_fail="deny", on_missing_data="stop",
    )
    flow, store, audit, _ = _flow_with(deny_policy, monkeypatch)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund 1200 order 48213", channel="C",
                                  thread_ts=None, requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "denied"  # NOT pending_approval
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Denied" in steps and "Routed for approval" not in steps


@pytest.mark.asyncio
async def test_require_approval_opens_durable_pending(monkeypatch):
    # real refund.v1 shape: over-limit → require_approval
    policy = Policy(
        id="refund.v1", version=1, owner="support_manager",
        all=[Condition(fact="amount_usd", op="<=", value=500),
             Condition(fact="order_age_days", op="<=", value=30)],
        on_pass="allow", on_fail="require_approval",
        required_role="support_manager", on_missing_data="stop",
    )
    flow, store, audit, approvals = _flow_with(policy, monkeypatch)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund 1200 order 48213", channel="C",
                                  thread_ts=None, requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"
    # a DURABLE, role-scoped pending approval now backs the run
    assert run.approval_id and run.approval_id.startswith("AP-")
    pending = await approvals.get_pending(run.approval_id)
    assert pending is not None and pending.required_role == "support_manager"
    assert pending.rule_id == "refund.v1"


def _require_approval_policy() -> Policy:
    """Policy that requires approval with 'support_manager' role — for open/stop tests."""
    return Policy(
        id="refund.v1", version=1, owner="support_manager",
        all=[Condition(fact="amount_usd", op="<=", value=500),
             Condition(fact="order_age_days", op="<=", value=30)],
        on_pass="allow", on_fail="require_approval",
        required_role="support_manager", on_missing_data="stop",
    )


def _manager_role_policy() -> Policy:
    """Same shape as refund.v1.yaml (required_role='manager') — needed so
    _DIANA's directory role ('manager') satisfies the ApprovalService authZ check
    in end-to-end click tests."""
    return Policy(
        id="refund.v1", version=1, owner="support_manager",
        all=[Condition(fact="amount_usd", op="<=", value=500),
             Condition(fact="order_age_days", op="<=", value=30)],
        on_pass="allow", on_fail="require_approval",
        required_role="manager", on_missing_data="stop",
    )


@pytest.mark.asyncio
async def test_stop_halts_without_routing_or_pending(monkeypatch):
    flow, store, audit, approvals = _flow_with(_require_approval_policy(), monkeypatch)
    # facts missing order_age_days → PolicyEngine returns 'stop' (fail closed)
    flow._engine.evaluate.return_value = RefundFacts(
        found=True, order_id="48213", customer="X", amount_usd=1200,
        order_age_days=None, reasoning="order age unknown",
    )
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "halted"          # NOT pending_approval
    assert run.approval_id is None         # no durable pending opened for a stop
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Halted" in steps and "Routed for approval" not in steps


@pytest.mark.asyncio
async def test_governed_resolution_closes_pending_and_stamps_rule(monkeypatch):
    # The directory routes TOM's request to DIANA (his manager).
    # Diana's approve click resolves through ApprovalService — identity-stamped.
    # Uses required_role="manager" so _DIANA's directory role satisfies authZ.
    flow, store, audit, approvals = _flow_with(_manager_role_policy(), monkeypatch)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund 1200", channel="C", thread_ts="t1",
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval" and run.approval_id
    # the flow stored the routed approver from the directory
    assert run.approver_slack_id == "U_DIANA"

    payload = {
        "type": "block_actions", "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_APPROVER", "message_ts": "1"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    }
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_action(payload)

    loaded = await store.get(run.id)
    assert loaded.status == "completed"
    # the pending is CLOSED — no orphaned 'pending' record left behind
    pending = await approvals.get_pending(run.approval_id)
    assert pending is not None and pending.status == "approved"
    # the resolution event is identity- AND rule-stamped
    approved = next(e for e in await audit.query(run.id) if e.step == "Approved")
    assert approved.actor.type == "human" and approved.actor.idp == "entra"
    assert approved.rule == {"id": "refund.v1", "version": 1}


@pytest.mark.asyncio
async def test_unauthorized_clicker_cannot_decide(monkeypatch):
    # TOM's over-limit request routes to DIANA; U_RANDO clicks — must be refused.
    # Uses required_role="manager" so the approval record is properly configured.
    flow, store, audit, approvals = _flow_with(_manager_role_policy(), monkeypatch)
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund 1200", channel="C", thread_ts="t1",
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval" and run.approval_id
    assert run.approver_slack_id == "U_DIANA"

    # a random user (not the routed approver) clicks Approve
    payload = {
        "type": "block_actions", "user": {"id": "U_RANDO", "name": "rando"},
        "container": {"channel_id": "D_APPROVER", "message_ts": "1"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    }
    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_action(payload)

    loaded = await store.get(run.id)
    assert loaded.status == "pending_approval"          # click refused, run unchanged
    pending = await approvals.get_pending(run.approval_id)
    assert pending is not None and pending.status == "pending"  # not decided by an outsider


@pytest.mark.asyncio
async def test_auto_approve_emits_typed_audit_and_calls_connector(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()

    audit = AuditLog(client=None, force_memory=True)
    engine = AsyncMock()
    engine.evaluate.return_value = RefundFacts(
        found=True, order_id="48190", customer="Marcus Lee",
        amount_usd=89.0, order_age_days=12, reasoning="within limits",
    )
    store = RunStore(client=None, force_memory=True)
    flow = RefundFlow(engine=engine, store=store, directory=_Directory(_TOM, _DIANA),
                      audit_log=audit, refund_connector=StripeRefundConnector())

    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund $89 order 48190", channel="C",
                                  thread_ts=None, requester_slack_id="U_TOM", user=_user())

    run = (await store.list_runs())[0]
    trail = await audit.query(run.id)
    steps = [e.step for e in trail]
    assert "Rule evaluated" in steps and "Refund issued" in steps

    rule_ev = next(e for e in trail if e.step == "Rule evaluated")
    # the guardrail event carries the rule id+version+result, and an agent actor
    assert rule_ev.rule == {"id": "refund.v1", "version": 1, "result": "allow"}
    assert rule_ev.actor.type == "agent"

    issued = next(e for e in trail if e.step == "Refund issued")
    # the act produced a real connector receipt, recorded by a system actor
    assert issued.target["refund_id"].startswith("re_")
    assert issued.actor.type == "system"
