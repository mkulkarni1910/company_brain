"""Workstream 4 — seam-extraction integration.

The flow now CALLS services: PolicyEngine (verdict), AuditLog (typed provenance),
and the Stripe connector (the act). This proves the typed, identity-stamped trail
and the connector receipt — not just the human-readable run timeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.approvals.service import ApprovalService
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.connectors.act.stripe_mock import StripeRefundConnector
from app.domain.identity import User
from app.domain.policy import Condition, Policy
from app.domain.workflow import RefundFacts
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore


def _user() -> User:
    return User(user_id="bot", tenant_id="t", email="bot@substrateos",
                display_name="Bot", group_ids=set())


async def _noop_slack(token, method, payload):
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
    flow = RefundFlow(engine=engine, store=store, audit_log=audit,
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
                                  thread_ts=None, requester_slack_id=None, user=_user())
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
                                  thread_ts=None, requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"
    # a DURABLE, role-scoped pending approval now backs the run
    assert run.approval_id and run.approval_id.startswith("AP-")
    pending = await approvals._store.get(run.approval_id)
    assert pending is not None and pending.required_role == "support_manager"
    assert pending.rule_id == "refund.v1"


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
    flow = RefundFlow(engine=engine, store=store, audit_log=audit,
                      refund_connector=StripeRefundConnector())

    with patch("app.workflows.flow.slack_call", new=_noop_slack):
        await flow.handle_request(text="refund $89 order 48190", channel="C",
                                  thread_ts=None, requester_slack_id=None, user=_user())

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
