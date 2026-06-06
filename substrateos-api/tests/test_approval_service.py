"""Workstream 2 — Human-in-the-loop approval gate tests.

Proves the platform-primitive guarantees: durable pending state, identity-bound +
role-authorized resolution, idempotency, and a resume callback on approve.
"""

from __future__ import annotations

import pytest

from app.approvals.service import (
    AlreadyResolved,
    ApprovalService,
    NotAuthorized,
    UnknownApproval,
)
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.domain.identity import User


def _manager() -> User:
    return User(user_id="diana", tenant_id="t", email="diana@acme.com",
                display_name="Diana Foster", group_ids={"support_manager"})


def _agent_without_role() -> User:
    return User(user_id="tom", tenant_id="t", email="tom@acme.com",
                display_name="Tom Reyes", group_ids={"support_agent"})


def _service(audit=None, on_resolved=None) -> ApprovalService:
    return ApprovalService(
        store=ApprovalStore(client=None, force_memory=True),
        audit=audit, on_resolved=on_resolved,
    )


async def _request(svc: ApprovalService) -> str:
    return await svc.request(
        run_id="RB-1", step="approve", required_role="support_manager",
        decision_context={"amount_usd": 1200}, rule_id="refund.v1", rule_version=1,
    )


@pytest.mark.asyncio
async def test_request_creates_pending():
    svc = _service()
    approval_id = await _request(svc)
    assert approval_id.startswith("AP-")


@pytest.mark.asyncio
async def test_authorized_approver_resolves_and_resumes():
    resumed: list = []

    async def on_resolved(pending, decision):
        resumed.append((pending.id, decision.choice, decision.approver.user_id))

    audit = AuditLog(client=None, force_memory=True)
    svc = _service(audit=audit, on_resolved=on_resolved)
    approval_id = await _request(svc)

    decision = await svc.resolve(approval_id, "approve", _manager())
    assert decision.choice == "approve"
    assert decision.approver.user_id == "diana"   # identity-bound
    assert resumed == [(approval_id, "approve", "diana")]   # run resumed
    steps = [e.step for e in await audit.query("RB-1")]
    assert "Routed for approval" in steps and "Approved" in steps


@pytest.mark.asyncio
async def test_unauthorized_identity_is_rejected_and_state_unchanged():
    svc = _service()
    approval_id = await _request(svc)
    with pytest.raises(NotAuthorized):
        await svc.resolve(approval_id, "approve", _agent_without_role())
    # still pending — an unauthorized click changes nothing, and approve still works
    decision = await svc.resolve(approval_id, "approve", _manager())
    assert decision.choice == "approve"


@pytest.mark.asyncio
async def test_idempotent_second_resolve_raises():
    svc = _service()
    approval_id = await _request(svc)
    await svc.resolve(approval_id, "reject", _manager())
    with pytest.raises(AlreadyResolved):
        await svc.resolve(approval_id, "approve", _manager())


@pytest.mark.asyncio
async def test_unknown_approval_raises():
    svc = _service()
    with pytest.raises(UnknownApproval):
        await svc.resolve("AP-0000", "approve", _manager())


@pytest.mark.asyncio
async def test_pending_survives_a_fresh_service_over_same_store():
    store = ApprovalStore(client=None, force_memory=True)
    svc1 = ApprovalService(store=store)
    approval_id = await svc1.request(
        run_id="RB-9", step="approve", required_role="support_manager",
    )
    # a different service instance sharing the store can still resolve it (durable)
    svc2 = ApprovalService(store=store)
    decision = await svc2.resolve(approval_id, "approve", _manager())
    assert decision.choice == "approve"
