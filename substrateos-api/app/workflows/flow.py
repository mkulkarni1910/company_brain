from __future__ import annotations

import logging

from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call
from app.config import get_settings
from app.domain.identity import User
from app.domain.policy import Policy
from app.domain.workflow import RefundDecision
from app.policy.engine import PolicyEngine
from app.policy.store import PolicyNotFound, PolicyStore
from app.workflows.engine import RefundEngine, RefundEngineError
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_ERROR = "Sorry, I couldn't evaluate that refund request right now. Please try again."


def _policy_limits(policy: Policy) -> tuple[float | None, int | None]:
    """Pull the display thresholds out of the policy conditions (for the cards)."""
    amount = age = None
    for cond in policy.all:
        if cond.fact == "amount_usd":
            amount = cond.value  # type: ignore[assignment]
        elif cond.fact == "order_age_days":
            age = cond.value  # type: ignore[assignment]
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
    ) -> None:
        self._engine = engine
        self._store = store
        self._policy_engine = policy_engine or PolicyEngine()
        self._policy_store = policy_store or PolicyStore()
        self._policy_id = policy_id
        self._policy_cache: Policy | None = None

    def _policy(self) -> Policy:
        if self._policy_cache is None:
            self._policy_cache = self._policy_store.load(self._policy_id)
        return self._policy_cache

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

        if guardrail.result == "allow":
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Auto-approved",
                                        detail=guardrail.reason, actor=rule)
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=(f"${decision.amount_usd:,.0f} refunded to {decision.customer} · "
                        "confirmation sent"),
                actor="SubstrateOS",
            )
            await self._post(token, channel, thread_ts,
                             text="Auto-approved within policy — refund issued.",
                             blocks=auto_approved_blocks(decision, run_id=run.id))
            return

        # Needs approval — route to the configured manager.
        run.status = "pending_approval"
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
                "blocks": decided_dm_blocks(d, approved=approved, approver_name=approver_name),
            })
        if approved:
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=f"${d.amount_usd:,.0f} refunded to {d.customer} · confirmation sent",
                actor="SubstrateOS",
            )
            run.status = "completed"
            await self._store.save(run)
        if run.channel:
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Refund {'approved' if approved else 'rejected'} by {approver_name}",
                             blocks=outcome_blocks(d, approved=approved, approver_name=approver_name))
