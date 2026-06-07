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
    customer_outcome_blocks,
    customer_request_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call
from app.config import get_settings
from app.connectors.act.stripe_mock import StripeRefundConnector
from app.directory.service import DirectoryService
from app.domain.audit import Actor
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.policy import Policy
from app.domain.workflow import RefundDecision, RefundFacts
from app.policy.engine import PolicyEngine
from app.policy.store import PolicyNotFound, PolicyStore
from app.workflows.engine import RefundEngine, RefundEngineError
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_ERROR = "Sorry, I couldn't evaluate that refund request right now. Please try again."


def _directory_identity(record: DirectoryUser) -> User:
    """Map a synced directory record to the identity the ApprovalService authorizes.

    principals() = {user_id, *group_ids}; granting the directory role as a group
    means `required_role in identity.principals()` is the same check the flow's
    hard-deny does — one vocabulary (manager/agent/customer), enforced twice.
    """
    return User(
        user_id=record.email,
        tenant_id=get_settings().substrateos_tenant_id,
        email=record.email,
        display_name=record.display_name or record.email,
        group_ids={record.role},
    )


def _policy_limits(policy: Policy) -> tuple[float | None, int | None]:
    """Pull the display thresholds out of the policy conditions (for the cards)."""
    amount = age = None
    for cond in policy.all:
        if cond.fact == "amount_usd" and isinstance(cond.value, int | float):
            amount = float(cond.value)
        elif cond.fact == "order_age_days" and isinstance(cond.value, int):
            age = cond.value
    return amount, age


def _render_decision(facts: RefundFacts, *, policy: Policy | None = None,
                     auto_approve: bool = False, reasoning: str | None = None
                     ) -> RefundDecision:
    """Assemble the card/run render view from extracted facts + evaluated policy."""
    limit_usd = limit_days = None
    if policy is not None:
        limit_usd, limit_days = _policy_limits(policy)
    return RefundDecision(
        found=facts.found, order_id=facts.order_id, customer=facts.customer,
        customer_email=facts.customer_email, amount_usd=facts.amount_usd,
        order_age_days=facts.order_age_days,
        policy_limit_usd=limit_usd, policy_limit_days=limit_days,
        auto_approve=auto_approve, reasoning=reasoning or facts.reasoning,
    )


class RefundFlow:
    """Drives the refund playbook over Slack: ack → identity check → extract facts →
    guardrail (policy-as-code) → act/route → decide. The verdict is decided by
    PolicyEngine, in code — never by the model."""

    def __init__(
        self,
        *,
        engine: RefundEngine,
        store: RunStore,
        directory: DirectoryService,
        policy_engine: PolicyEngine | None = None,
        policy_store: PolicyStore | None = None,
        policy_id: str = "refund.v1",
        audit_log: AuditLog | None = None,
        refund_connector: StripeRefundConnector | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self._engine = engine
        self._store = store
        self._directory = directory
        self._policy_engine = policy_engine or PolicyEngine()
        self._policy_store = policy_store or PolicyStore()
        self._policy_id = policy_id
        # seams: provenance + the act connector + the approval gate
        self._audit = audit_log or AuditLog()
        self._refund_connector = refund_connector or StripeRefundConnector()
        self._approvals = approval_service or ApprovalService(
            store=ApprovalStore(), audit=self._audit)

    def _policy(self) -> Policy:
        # PolicyStore caches with mtime invalidation: a YAML edit is picked up on the
        # next request, no restart — "flip 500 → 300 in the file" works live.
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

    async def _profile(self, token: str, slack_user_id: str | None
                       ) -> tuple[str | None, str | None]:
        """(display_name, email) via users.info — both None when unreachable."""
        if not slack_user_id:
            return None, None
        body = await slack_call(token, "users.info", {"user": slack_user_id})
        if not body:
            return None, None
        u = body.get("user") or {}
        profile = u.get("profile") or {}
        name = profile.get("display_name") or u.get("real_name") or u.get("name")
        return name, (profile.get("email") or "").lower() or None

    async def _post(self, token: str, channel: str, thread_ts: str | None,
                    *, text: str, card: dict | None = None) -> dict | None:
        payload: dict = {"channel": channel, "text": text}
        if card:
            payload.update(card)  # {"blocks": [...], "attachments": [...]}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await slack_call(token, "chat.postMessage", payload)

    async def _route_to_support(self, token: str, run, *, text: str, requester: str,
                                record, channel: str, thread_ts: str | None,
                                user: User) -> None:
        """Customer path: read-only engine lookup pre-fills the hand-off card;
        lookup failure never blocks routing."""
        support_channel = get_settings().slack_refund_channel_id
        if not support_channel:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(
                run.id, step="No support channel",
                detail="SLACK_REFUND_CHANNEL_ID is not configured — customer request not routed",
                actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="Refunds are handled by our support team — "
                                  "please contact them directly.")
            return
        decision = None
        try:
            facts = await self._engine.evaluate(text, user=user, requester=record)
            if facts.found:
                decision = _render_decision(facts)
        except RefundEngineError:
            logger.warning("customer order lookup failed; routing without facts")
        if decision is not None:
            run.decision = decision
            await self._store.add_event(
                run.id, step="Order fetched",
                detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                        f"age {decision.order_age_days} days — fetched for {requester}"),
                actor="SubstrateOS")
        posted = await slack_call(token, "chat.postMessage", {
            "channel": support_channel,
            "text": f"Customer refund request from {requester}",
            **customer_request_blocks(request_text=text, customer_name=requester,
                                      run_id=run.id, decision=decision),
        })
        if not posted:
            run.status = "needs_attention"
            run.decision = None  # facts never reached the channel — don't show them on the run
            await self._store.save(run)
            await self._store.add_event(run.id, step="Routing failed",
                                        detail="Could not post to the refunds channel",
                                        actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the support team — please contact them directly.")
            return
        run.status = "routed_to_support"
        run.handoff_channel = support_channel
        run.handoff_ts = posted.get("ts")
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed to support",
            detail=f"Posted to the refunds channel for a support agent ({requester} is a customer)",
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="Refunds are handled by our support team — I've passed your "
                              "request to them and someone will follow up here.")

    async def _notify_customer(self, token: str, run, *, approved: bool,
                               approver_name: str) -> None:
        """Relay the outcome to the customer (their original thread, else a DM via
        the directory) and mark the support-channel hand-off card resolved.
        Fail-soft: the relay must never break the recorded decision."""
        d = run.decision
        if d is None or not d.order_id:
            return
        try:
            linked = await self._store.find_routed_run(d.order_id)
            notified_where: str | None = None
            if linked is not None and linked.channel:
                posted = await slack_call(token, "chat.postMessage", {
                    "channel": linked.channel, "thread_ts": linked.thread_ts,
                    "text": f"Your refund was {'approved' if approved else 'declined'}",
                    **customer_outcome_blocks(d, approved=approved),
                })
                if posted:
                    notified_where = "their thread"
                    # Post→save is not atomic: if the save fails after a successful
                    # post, a later decision could re-notify (demo-grade, accepted).
                    linked.status = "completed" if approved else "rejected"
                    await self._store.save(linked)
                    await self._store.add_event(
                        linked.id, step="Outcome relayed",
                        detail=(f"{'Approved' if approved else 'Rejected'} by "
                                f"{approver_name} — customer notified"),
                        actor="SubstrateOS")
            elif d.customer_email:
                record = await self._directory.resolve(d.customer_email)
                if record is not None and record.slack_id:
                    opened = await slack_call(token, "conversations.open",
                                              {"users": record.slack_id})
                    dm = ((opened or {}).get("channel") or {}).get("id")
                    if dm:
                        posted = await slack_call(token, "chat.postMessage", {
                            "channel": dm,
                            "text": f"Your refund was {'approved' if approved else 'declined'}",
                            **customer_outcome_blocks(d, approved=approved),
                        })
                        if posted:
                            notified_where = "a DM"
            await self._store.add_event(
                run.id,
                step="Customer notified" if notified_where else "Customer not reachable",
                detail=(f"Outcome sent to {d.customer} in {notified_where}" if notified_where
                        else f"No conversation or directory match for {d.customer or 'the customer'}"),
                actor="SubstrateOS")
            if linked is not None and linked.handoff_channel and linked.handoff_ts:
                mark = "✅" if approved else "✕"
                suffix = "customer notified" if notified_where else "customer not reachable"
                await slack_call(token, "chat.postMessage", {
                    "channel": linked.handoff_channel, "thread_ts": linked.handoff_ts,
                    "text": (f"{mark} Resolved — "
                             f"{'approved' if approved else 'rejected'} by {approver_name}, {suffix}"),
                })
        except Exception:  # noqa: BLE001 — relay must never break the decision
            logger.exception("customer outcome relay failed for run %s", run.id)

    # ── inbound request (from the Slack webhook) ─────────────────────────────

    async def handle_request(self, *, text: str, channel: str, thread_ts: str | None,
                             requester_slack_id: str | None, user: User) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        requester, requester_email = await self._profile(token, requester_slack_id)
        requester = requester or "Support agent"
        run = await self._store.create(
            requester_name=requester, requester_slack_id=requester_slack_id,
            channel=channel, thread_ts=thread_ts,
        )
        await self._store.add_event(
            run.id, step="Request received",
            detail=f"{text[:160]} · from Slack", actor=requester,
        )
        await self._audit.record(
            run_id=run.id, step="Request received",
            actor=Actor(type="human", id=requester_email or requester),
            inputs_summary=text[:160], surface="slack",
        )

        # Check: who is asking, per the synced directory (Slack id ↔ Entra groups).
        record = await self._directory.resolve(requester_email)
        if record is None:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(
                run.id, step="Identity unknown",
                detail="Could not establish the requester's identity (no Slack email match)",
                actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="I couldn't verify who's asking, so I've stopped. "
                                  "Make sure your Slack profile has an email address.")
            return
        groups = ", ".join(record.groups) if record.groups else "no role groups"
        await self._store.add_event(
            run.id, step="Identity checked",
            detail=f"{requester} → {record.role} ({groups})", actor="SubstrateOS")
        await self._audit.record(
            run_id=run.id, step="Identity checked",
            actor=Actor(type="human", id=record.email, idp="entra"),
            detail=f"{requester} → {record.role}",
        )

        if record.role == "customer":
            await self._route_to_support(token, run, text=text, requester=requester,
                                         record=record, channel=channel,
                                         thread_ts=thread_ts, user=user)
            return

        await self._post(token, channel, thread_ts,
                         text="Pulling up the order and checking the refund policy…")

        try:
            facts = await self._engine.evaluate(text, user=user, requester=record)
        except RefundEngineError:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail="Could not evaluate the request", actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        if not facts.found:
            run.decision = _render_decision(facts)
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
        decision = _render_decision(facts, policy=policy,
                                    auto_approve=(guardrail.result == "allow"),
                                    reasoning=guardrail.reason or facts.reasoning)
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
                    f"(limits ${(decision.policy_limit_usd or 0):,.0f} / "
                    f"{decision.policy_limit_days} days): {guardrail.reason}"),
            actor=rule,
        )
        # provenance: typed, identity-stamped audit trail (the receipt)
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
                             card=auto_approved_blocks(decision, run_id=run.id))
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
            # fail-closed: the engine could NOT decide (missing/ambiguous facts).
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

        # require_approval — Stop: only the requester's Entra manager, who must be
        # in the Managers group and reachable on Slack, may approve. No fallback.
        mgr = (await self._directory.resolve(record.manager_email)
               if record.manager_email else None)
        reason: str | None = None
        if mgr is None:
            reason = "no manager is set for you in Entra ID"
        elif mgr.role != "manager":
            reason = (f"{mgr.display_name or mgr.email} is not in the "
                      f"{s.entra_managers_group} group")
        elif not mgr.slack_id:
            reason = f"{mgr.display_name or mgr.email} has no Slack account"
        if reason:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(run.id, step="No eligible approver",
                                        detail=f"Stopped: {reason}", actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I can't route this for approval — {reason}. "
                                  "An admin needs to fix the directory before I can continue.")
            return

        # Durable, identity-aware pending approval (the platform primitive);
        # the Slack card below mirrors it for the UX.
        run.status = "pending_approval"
        run.approver_name = mgr.display_name or mgr.email
        run.approver_slack_id = mgr.slack_id
        required_role = guardrail.required_role or policy.required_role or "manager"
        run.approval_id = await self._approvals.request(
            run_id=run.id, step="approve", required_role=required_role,
            decision_context={
                "order_id": facts.order_id, "amount_usd": facts.amount_usd,
                "order_age_days": facts.order_age_days, "result": guardrail.result,
            },
            rule_id=guardrail.rule_id, rule_version=guardrail.rule_version,
        )
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed for approval",
            detail=(f"Sent to {run.approver_name} — {requester}'s manager "
                    f"({s.entra_managers_group} group)"),
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="I can't auto-approve this one — routing to your manager for approval.",
                         card=needs_approval_blocks(decision, approver_label=run.approver_name,
                                                    run_id=run.id))
        opened = await slack_call(token, "conversations.open", {"users": mgr.slack_id})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if not dm:
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the approver in a DM — please review manually.")
            return
        posted = await slack_call(token, "chat.postMessage", {
            "channel": dm, "text": "Refund needs your approval",
            **approval_dm_blocks(decision, requester_name=requester, run_id=run.id),
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
                    **decided_dm_blocks(run.decision,
                                        approved=(run.status in ("approved", "completed")),
                                        approver_name=run.approver_name or "a manager"),
                })
            return

        # Only the routed approver — who must be a manager in the directory —
        # may act. Anyone else is refused and the attempt is audited. A run with
        # no recorded approver (pre-directory legacy) is hard-denied, not open.
        actor_record = await self._directory.get_by_slack_id(approver_id)
        is_routed = (run.approver_slack_id is not None
                     and approver_id == run.approver_slack_id)
        if not is_routed or actor_record is None or actor_record.role != "manager":
            actor_name = (await self._display_name(token, approver_id)
                          or (payload.get("user") or {}).get("name") or "Someone")
            await self._store.add_event(
                run.id, step="Approval denied",
                detail=(f"{actor_name} tried to act but is not the routed approver "
                        "(managers only)"),
                actor=actor_name)
            await self._audit.record(
                run_id=run.id, step="Approval denied",
                actor=Actor(type="human", id=(actor_record.email if actor_record
                                              else approver_id or actor_name)),
                decision="denied", detail=f"{actor_name} is not the routed approver",
            )
            if dm_channel and approver_id:
                await slack_call(token, "chat.postEphemeral", {
                    "channel": dm_channel, "user": approver_id,
                    "text": "Only the routed approver (a manager) can act on this request.",
                })
            return

        approved = action_id == "refund_approve"
        approver_name = (await self._display_name(token, approver_id)
                         or (payload.get("user") or {}).get("name") or "Manager")

        # Governed resolution: resolve THROUGH the ApprovalService — it enforces role
        # authZ on the directory-bound identity, stamps that identity, records a
        # rule-bearing audit event, and closes the PendingApproval (no orphan).
        if run.approval_id:
            pending = await self._approvals.get_pending(run.approval_id)
            if pending is not None and pending.status == "pending":
                identity = _directory_identity(actor_record)
                try:
                    await self._approvals.resolve(
                        run.approval_id, "approve" if approved else "reject", identity
                    )
                except NotAuthorized:
                    # Defense in depth: the routed/role check above should make this
                    # unreachable; if it fires, refuse rather than fall through.
                    await self._store.add_event(
                        run.id, step="Approval blocked",
                        detail=f"{approver_name} is not in role {pending.required_role}",
                        actor="SubstrateOS",
                    )
                    return
                except (AlreadyResolved, UnknownApproval):
                    pass  # already decided / not found — proceed idempotently
        else:
            # legacy run with no durable approval: best-effort typed audit
            await self._audit.record(
                run_id=run.id, step="Approved" if approved else "Rejected",
                actor=Actor(type="human", id=actor_record.email, idp="entra"),
                decision="approve" if approved else "reject",
                detail=f"{approver_name} {'approved' if approved else 'rejected'}",
            )

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
        if dm_channel and dm_ts:
            await slack_call(token, "chat.update", {
                "channel": dm_channel, "ts": dm_ts,
                "text": f"Refund {'approved' if approved else 'rejected'}",
                **decided_dm_blocks(d, approved=approved, approver_name=approver_name),
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
            mention = (f"<@{run.requester_slack_id}>" if run.requester_slack_id else None)
            label = mention or run.requester_name
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Hello {label} — refund {'approved' if approved else 'rejected'} by {approver_name}",
                             card=outcome_blocks(d, approved=approved,
                                                 approver_name=approver_name, mention=label))
        await self._notify_customer(token, run, approved=approved,
                                    approver_name=approver_name)
