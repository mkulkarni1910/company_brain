"""Workstream 4 — seam-extraction integration.

The flow now CALLS services: PolicyEngine (verdict), AuditLog (typed provenance),
and the Stripe connector (the act). This proves the typed, identity-stamped trail
and the connector receipt — not just the human-readable run timeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.audit.log import AuditLog
from app.connectors.act.stripe_mock import StripeRefundConnector
from app.domain.identity import User
from app.domain.workflow import RefundFacts
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore


def _user() -> User:
    return User(user_id="bot", tenant_id="t", email="bot@substrateos",
                display_name="Bot", group_ids=set())


async def _noop_slack(token, method, payload):
    return {"ok": True, "ts": "1", "channel": payload.get("channel")}


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
