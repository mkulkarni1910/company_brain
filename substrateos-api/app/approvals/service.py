"""ApprovalService — request a governed human decision and resume on resolve.

What makes this a platform primitive (not a webhook):
  * durable pause/resume — the PendingApproval is persisted (survives restarts)
  * identity-bound — the decision carries a real Entra identity
  * authorized — only an identity in ``required_role`` may resolve; others rejected
  * audited — request and resolution both emit identity-stamped audit events
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.domain.approval import ApprovalChoice, ApprovalDecision, PendingApproval
from app.domain.audit import Actor
from app.domain.identity import User

logger = logging.getLogger(__name__)

# called after a decision is recorded, to resume the paused run
OnResolved = Callable[[PendingApproval, ApprovalDecision], Awaitable[None]]


class UnknownApproval(KeyError):
    """No pending approval with that id."""


class NotAuthorized(PermissionError):
    """The resolving identity is not in the required role."""


class AlreadyResolved(RuntimeError):
    """The approval was already decided (idempotency guard)."""


class ApprovalService:
    def __init__(
        self,
        *,
        store: ApprovalStore,
        audit: AuditLog | None = None,
        on_resolved: OnResolved | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._on_resolved = on_resolved

    async def request(
        self,
        *,
        run_id: str,
        step: str,
        required_role: str,
        decision_context: dict | None = None,
        rule_id: str | None = None,
        rule_version: int | None = None,
    ) -> str:
        pending = PendingApproval(
            id=await self._store.next_id(),
            run_id=run_id,
            step=step,
            required_role=required_role,
            decision_context=decision_context or {},
            rule_id=rule_id,
            rule_version=rule_version,
            created_at=datetime.now(UTC),
        )
        await self._store.create(pending)
        if self._audit is not None:
            await self._audit.record(
                run_id=run_id,
                step="Routed for approval",
                actor=Actor.system(),
                decision="require_approval",
                rule=_rule(rule_id, rule_version),
                detail=f"Awaiting an authorized {required_role}",
            )
        return pending.id

    async def resolve(
        self, approval_id: str, choice: ApprovalChoice, identity: User, *, note: str | None = None
    ) -> ApprovalDecision:
        pending = await self._store.get(approval_id)
        if pending is None:
            raise UnknownApproval(approval_id)
        if pending.status != "pending":
            raise AlreadyResolved(f"approval {approval_id} already {pending.status}")
        # AUTHZ — only an identity holding the required role may resolve.
        if pending.required_role not in identity.principals():
            raise NotAuthorized(
                f"{identity.user_id} lacks required role {pending.required_role!r}"
            )

        pending.status = "approved" if choice == "approve" else "rejected"
        await self._store.save(pending)
        decision = ApprovalDecision(
            approval_id=approval_id, choice=choice, approver=identity,
            note=note, decided_at=datetime.now(UTC),
        )
        if self._audit is not None:
            await self._audit.record(
                run_id=pending.run_id,
                step="Approved" if choice == "approve" else "Rejected",
                actor=Actor(type="human", id=identity.user_id, idp="entra"),
                decision=choice,
                rule=_rule(pending.rule_id, pending.rule_version),
                detail=f"{identity.display_name} {choice}d (run {pending.run_id})",
            )
        if self._on_resolved is not None:
            await self._on_resolved(pending, decision)
        return decision


def _rule(rule_id: str | None, rule_version: int | None) -> dict | None:
    return {"id": rule_id, "version": rule_version} if rule_id else None
