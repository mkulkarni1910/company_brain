from __future__ import annotations

import logging

from app.approvals.service import (
    AlreadyResolved,
    ApprovalService,
    NotAuthorized,
    UnknownApproval,
)
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call
from app.config import get_settings
from app.connectors.act.stripe_mock import StripeRefundConnector
from app.domain.audit import Actor
from app.domain.identity import User
from app.domain.policy import Policy
from app.domain.workflow import RefundDecision
from app.policy.engine import PolicyEngine
from app.policy.store import PolicyNotFound, PolicyStore
from app.workflows.engine import RefundEngine, RefundEngineError
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_ERROR = "Sorry, I couldn't evaluate that refund request right now. Please try again."


def _approver_identity(payload: dict, required_role: str, settings) -> User:
    """Map a Slack action payload to an identity for the approval authZ check.

    Demo mapping: the configured Slack approver holds the required role. A real
    deployment resolves the Slack user to an Entra identity and its group
    memberships — that is the one remaining hop to a fully Entra-backed gate.
    """
    user = payload.get("user") or {}
    slack_id = user.get("id") or ""
    name = user.get("name") or slack_id or "Manager"
    is_configured = bool(slack_id) and slack_id == (settings.slack_refund_approver_id or "")
    return User(
        user_id=slack_id or name, tenant_id="t-demo",
        email=f"{slack_id or name}@slack", display_name=name,
        group_ids={required_role} if is_configured else set(),
    )


def _policy_limits(policy: Policy) -> tuple[float | None, int | None]:
    """Pull the display thresholds out of the policy conditions (for the cards).

    Defensive: only read numerically-typed condition values so a mis-authored
    policy can never surface as an HTTP 500 on a refund request (the Condition
    validator already rejects such policies at load — this is belt-and-braces).
    """
    amount = age = None
    for cond in policy.all:
        if cond.fact == "amount_usd" and isinstance(cond.value, int | float):
            amount = float(cond.value)
        elif cond.fact == "order_age_days" and isinstance(cond.value, int):
            age = cond.value
    return amount, age


class RefundFlow:
    """Drives the refund playbook over Slack: ack → extract facts → guardrail
    (policy-as-code) → act/route → decide. The verdict is decided by PolicyEngine,
    in code — never by the model."""

    def __init__(
        self,
        *,
        engine: RefundEngine,
        store: RunStore,
        policy_engine: PolicyEngine | None = None,
        policy_store: PolicyStore | None = None,
        policy_id: str = "refund.v1",
        audit_log: AuditLog | None = None,
        refund_connector: StripeRefundConnector | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self._engine = engine
        self._store = store
        self._policy_engine = policy_engine or PolicyEngine()
        self._policy_store = policy_store or PolicyStore()
        self._policy_id = policy_id
        # seams: provenance + the act connector + the approval gate
        # (the flow calls services, not inline logic)
        self._audit = audit_log or AuditLog()
        self._refund_connector = refund_connector or StripeRefundConnector()
        self._approvals = approval_service or ApprovalService(store=ApprovalStore(), audit=self._audit)

    def _policy(self) -> Policy:
        # PolicyStore caches with mtime invalidation, so a YAML edit is picked up on
        # the next request without a process restart.
        return self._policy_store.load(self._policy_id)

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _display_name(self, token: str, slack_user_id: str | None) -> str | None:
        if not slack_user_id:
            return None
        body = await slack_call(token, "users.info", {"user": slack_user_id})
        if not body:
            return None
        u = body.get("user") or {}
        profile = u.get("profile") or {}
        return profile.get("display_name") or u.get("real_name") or u.get("name")

    async def _post(self, token: str, channel: str, thread_ts: str | None,
                    *, text: str, blocks: list[dict] | None = None) -> dict | None:
        payload: dict = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await slack_call(token, "chat.postMessage", payload)

    # ── inbound request (from the Slack webhook) ─────────────────────────────

    async def handle_request(self, *, text: str, channel: str, thread_ts: str | None,
                             requester_slack_id: str | None, user: User) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        requester = await self._display_name(token, requester_slack_id) or "Support agent"
        run = await self._store.create(
            requester_name=requester, requester_slack_id=requester_slack_id,
            channel=channel, thread_ts=thread_ts,
        )
        await self._store.add_event(
            run.id, step="Request received",
            detail=f"{text[:160]} · from Slack", actor=requester,
        )
        first = requester.split()[0]
        await self._post(token, channel, thread_ts,
                         text=f"On it, {first} — pulling up the order and checking the refund policy…")

        try:
            facts = await self._engine.evaluate(text, user=user)
        except RefundEngineError:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail="Could not evaluate the request", actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        if not facts.found:
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Order not found",
                                        detail=facts.reasoning, actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I couldn't find that order in our records. {facts.reasoning}")
            return

        # ── Guardrail: deterministic policy-as-code decides the verdict (not the model) ──
        try:
            policy = self._policy()
        except PolicyNotFound:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail=f"Policy {self._policy_id} not found",
                                        actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        guardrail = self._policy_engine.evaluate(
            policy, {"amount_usd": facts.amount_usd, "order_age_days": facts.order_age_days}
        )
        rule = f"{guardrail.rule_id}@v{guardrail.rule_version}"
        limit_usd, limit_days = _policy_limits(policy)
        decision = RefundDecision(
            found=True, order_id=facts.order_id, customer=facts.customer,
            amount_usd=facts.amount_usd, order_age_days=facts.order_age_days,
            policy_limit_usd=limit_usd, policy_limit_days=limit_days,
            auto_approve=(guardrail.result == "allow"),
            reasoning=guardrail.reason or facts.reasoning,
        )
        run.decision = decision

        await self._store.add_event(
            run.id, step="Facts gathered",
            detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                    f"age {decision.order_age_days} days · customer {decision.customer}"),
            actor="SubstrateOS",
        )
        await self._store.add_event(
            run.id, step="Rule evaluated",
            detail=(f"{rule} → {guardrail.result} "
                    f"(limits ${(limit_usd or 0):,.0f} / {limit_days} days): {guardrail.reason}"),
            actor=rule,
        )
        # provenance: typed, identity-stamped audit trail (the receipt), via the AuditLog seam
        await self._audit.record(
            run_id=run.id, step="Facts gathered", actor=Actor.agent("refund-engine"),
            target={"order_id": decision.order_id},
            detail=f"${(decision.amount_usd or 0):,.0f} · {decision.order_age_days}d · {decision.customer}",
        )
        await self._audit.record(
            run_id=run.id, step="Rule evaluated", actor=Actor.agent(rule),
            rule={"id": guardrail.rule_id, "version": guardrail.rule_version,
                  "result": guardrail.result},
            decision=guardrail.result, detail=guardrail.reason,
        )

        if guardrail.result == "allow":
            receipt = await self._refund_connector.refund(
                order_id=decision.order_id, amount_usd=decision.amount_usd
            )
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Auto-approved",
                                        detail=guardrail.reason, actor=rule)
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=(f"${decision.amount_usd:,.0f} refunded to {decision.customer} · "
                        f"{receipt.refund_id}"),
                actor="SubstrateOS",
            )
            await self._audit.record(
                run_id=run.id, step="Refund issued", actor=Actor.system(),
                action="stripe.refund",
                target={"order_id": decision.order_id, "refund_id": receipt.refund_id},
            )
            await self._post(token, channel, thread_ts,
                             text="Auto-approved within policy — refund issued.",
                             blocks=auto_approved_blocks(decision, run_id=run.id))
            return

        if guardrail.result == "deny":
            run.status = "denied"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Denied",
                                        detail=guardrail.reason, actor=rule)
            await self._audit.record(
                run_id=run.id, step="Denied", actor=Actor.agent(rule),
                rule={"id": guardrail.rule_id, "version": guardrail.rule_version,
                      "result": "deny"},
                decision="deny", detail=guardrail.reason,
            )
            await self._post(token, channel, thread_ts,
                             text=f"This refund is denied by policy. {guardrail.reason}")
            return

        if guardrail.result == "stop":
            # fail-closed: the engine could NOT decide (missing/ambiguous facts). Do not
            # route it as a normal approvable refund (no card, no PendingApproval with a
            # null amount) — halt and surface the gap for manual escalation.
            run.status = "halted"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Halted",
                                        detail=guardrail.reason, actor=rule)
            await self._audit.record(
                run_id=run.id, step="Halted", actor=Actor.agent(rule),
                rule={"id": guardrail.rule_id, "version": guardrail.rule_version,
                      "result": "stop"},
                decision="stop", detail=guardrail.reason,
            )
            await self._post(token, channel, thread_ts,
                             text=("I can't decide this one safely — the request is missing or "
                                   f"ambiguous data ({guardrail.reason}). Escalating for manual review."))
            return

        if guardrail.result != "require_approval":
            # defensive fail-closed: never silently route an unknown verdict to a human
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail=f"Unexpected guardrail result {guardrail.result!r}",
                                        actor=rule)
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        # require_approval → human gate. Open a DURABLE, identity-aware pending approval
        # (the platform primitive); the Slack card below mirrors it for the UX.
        run.status = "pending_approval"
        required_role = guardrail.required_role or policy.required_role or "support_manager"
        run.approval_id = await self._approvals.request(
            run_id=run.id, step="approve", required_role=required_role,
            decision_context={
                "order_id": facts.order_id, "amount_usd": facts.amount_usd,
                "order_age_days": facts.order_age_days, "result": guardrail.result,
            },
            rule_id=guardrail.rule_id, rule_version=guardrail.rule_version,
        )
        await self._store.save(run)
        approver_id = s.slack_refund_approver_id
        approver_label = "a Support Manager"
        if approver_id:
            approver_label = await self._display_name(token, approver_id) or "Support Manager"
        await self._store.add_event(run.id, step="Routed for approval",
                                    detail=f"Sent to {approver_label} in Slack", actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="I can't auto-approve this one — routing for approval.",
                         blocks=needs_approval_blocks(decision, approver_label=approver_label,
                                                      run_id=run.id))
        if not approver_id:
            logger.warning("SLACK_REFUND_APPROVER_ID not configured; run %s waits", run.id)
            return
        opened = await slack_call(token, "conversations.open", {"users": approver_id})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if not dm:
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the approver in a DM — please review manually.")
            return
        posted = await slack_call(token, "chat.postMessage", {
            "channel": dm, "text": "Refund needs your approval",
            "blocks": approval_dm_blocks(decision, requester_name=requester, run_id=run.id),
        })
        if posted:
            run.dm_channel = dm
            run.dm_ts = posted.get("ts")
            await self._store.save(run)

    # ── button clicks (from /bot/slack/interactive) ──────────────────────────

    async def handle_action(self, payload: dict) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        actions = payload.get("actions") or []
        if not actions:
            return
        action = actions[0]
        action_id = action.get("action_id")
        if action_id not in ("refund_approve", "refund_reject"):
            return
        run_id = action.get("value") or ""
        run = await self._store.get(run_id)
        if run is None or run.decision is None:
            logger.warning("refund action for unknown run %r", run_id)
            return
        approver_id = (payload.get("user") or {}).get("id")
        container = payload.get("container") or {}
        dm_channel = run.dm_channel or container.get("channel_id")
        dm_ts = run.dm_ts or container.get("message_ts")

        if run.status != "pending_approval":
            # Idempotent: re-render the decided card, change nothing.
            if dm_channel and dm_ts:
                await slack_call(token, "chat.update", {
                    "channel": dm_channel, "ts": dm_ts,
                    "text": f"Refund {run.status}",
                    "blocks": decided_dm_blocks(run.decision,
                                                approved=(run.status in ("approved", "completed")),
                                                approver_name=run.approver_name or "a manager"),
                })
            return

        approved = action_id == "refund_approve"
        approver_name = (await self._display_name(token, approver_id)
                         or (payload.get("user") or {}).get("name") or "Manager")

        # Governed resolution: if a durable approval backs this run, resolve THROUGH the
        # ApprovalService — it enforces role authZ, stamps a real identity, records a
        # rule-bearing audit event, and closes the PendingApproval (no orphan).
        governed = False
        if run.approval_id:
            pending = await self._approvals.get_pending(run.approval_id)
            if pending is not None and pending.status == "pending":
                identity = _approver_identity(payload, pending.required_role, s)
                try:
                    await self._approvals.resolve(
                        run.approval_id, "approve" if approved else "reject", identity
                    )
                    governed = True
                except NotAuthorized:
                    await self._store.add_event(
                        run.id, step="Approval blocked",
                        detail=f"{approver_name} is not in role {pending.required_role}",
                        actor="SubstrateOS",
                    )
                    if run.channel:
                        await self._post(token, run.channel, run.thread_ts,
                                         text=(f"{approver_name} isn't authorized to decide this "
                                               f"(requires {pending.required_role}). No action taken."))
                    return
                except (AlreadyResolved, UnknownApproval):
                    governed = True  # already decided / not found — handle idempotently

        run.status = "approved" if approved else "rejected"
        run.approver_name = approver_name
        await self._store.save(run)
        d = run.decision
        await self._store.add_event(
            run.id, step="Approved" if approved else "Rejected",
            detail=(f"Manager {'approved' if approved else 'rejected'} the over-limit refund "
                    f"of ${d.amount_usd:,.0f} on order #{d.order_id}"),
            actor=approver_name,
        )
        if not governed:
            # legacy path (no durable approval backs this run): best-effort audit with the
            # Slack identity and no rule. Governed runs already got an identity- AND
            # rule-stamped event from ApprovalService.resolve().
            await self._audit.record(
                run_id=run.id, step="Approved" if approved else "Rejected",
                actor=Actor(type="human", id=approver_id or approver_name, idp="slack"),
                decision="approve" if approved else "reject",
                detail=f"{approver_name} {'approved' if approved else 'rejected'}",
            )
        if dm_channel and dm_ts:
            await slack_call(token, "chat.update", {
                "channel": dm_channel, "ts": dm_ts,
                "text": f"Refund {'approved' if approved else 'rejected'}",
                "blocks": decided_dm_blocks(d, approved=approved, approver_name=approver_name),
            })
        if approved:
            receipt = await self._refund_connector.refund(
                order_id=d.order_id, amount_usd=d.amount_usd
            )
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=f"${d.amount_usd:,.0f} refunded to {d.customer} · {receipt.refund_id}",
                actor="SubstrateOS",
            )
            await self._audit.record(
                run_id=run.id, step="Refund issued", actor=Actor.system(),
                action="stripe.refund",
                target={"order_id": d.order_id, "refund_id": receipt.refund_id},
            )
            run.status = "completed"
            await self._store.save(run)
        if run.channel:
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Refund {'approved' if approved else 'rejected'} by {approver_name}",
                             blocks=outcome_blocks(d, approved=approved, approver_name=approver_name))
