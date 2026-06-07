from __future__ import annotations

import logging

from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    customer_request_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call
from app.config import get_settings
from app.directory.service import DirectoryService
from app.domain.identity import User
from app.workflows.engine import RefundEngine, RefundEngineError
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_ERROR = "Sorry, I couldn't evaluate that refund request right now. Please try again."


class RefundFlow:
    """Drives the refund playbook over Slack: ack → evaluate → act/route → decide."""

    def __init__(self, *, engine: RefundEngine, store: RunStore,
                 directory: DirectoryService) -> None:
        self._engine = engine
        self._store = store
        self._directory = directory

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
            decision = await self._engine.evaluate(text, user=user, requester=record)
        except RefundEngineError:
            logger.warning("customer order lookup failed; routing without facts")
        if decision is not None and decision.found:
            run.decision = decision
            await self._store.add_event(
                run.id, step="Order fetched",
                detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                        f"age {decision.order_age_days} days — fetched for {requester}"),
                actor="SubstrateOS")
        else:
            decision = None
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
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed to support",
            detail=f"Posted to the refunds channel for a support agent ({requester} is a customer)",
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="Refunds are handled by our support team — I've passed your "
                              "request to them and someone will follow up here.")

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

        if record.role == "customer":
            await self._route_to_support(token, run, text=text, requester=requester,
                                         record=record, channel=channel,
                                         thread_ts=thread_ts, user=user)
            return

        first = requester.split()[0]
        await self._post(token, channel, thread_ts,
                         text=f"On it, {first} — pulling up the order and checking the refund policy…")

        try:
            decision = await self._engine.evaluate(text, user=user)
        except RefundEngineError:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail="Could not evaluate the request", actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        run.decision = decision
        if not decision.found:
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Order not found",
                                        detail=decision.reasoning, actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I couldn't find that order in our records. {decision.reasoning}")
            return

        await self._store.add_event(
            run.id, step="Facts gathered",
            detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                    f"age {decision.order_age_days} days · customer {decision.customer}"),
            actor="SubstrateOS",
        )
        await self._store.add_event(
            run.id, step="Rule evaluated",
            detail=(f"Auto-approve limits ${decision.policy_limit_usd:,.0f} / "
                    f"{decision.policy_limit_days} days → "
                    f"{'within limit' if decision.auto_approve else 'over limit'}"),
            actor="refund_v1",
        )

        if decision.auto_approve:
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Auto-approved",
                                        detail=decision.reasoning, actor="refund_v1")
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=(f"${decision.amount_usd:,.0f} refunded to {decision.customer} · "
                        "confirmation sent"),
                actor="SubstrateOS",
            )
            await self._post(token, channel, thread_ts,
                             text="Auto-approved within policy — refund issued.",
                             card=auto_approved_blocks(decision, run_id=run.id))
            return

        # Needs approval — Stop: only the requester's Entra manager, who must be
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

        run.status = "pending_approval"
        run.approver_name = mgr.display_name or mgr.email
        run.approver_slack_id = mgr.slack_id
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
        # Accepted trade-off: every denied click appends an audit event (no cap) —
        # the trail of attempts is the governance story, noise is tolerable.
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
            if dm_channel and approver_id:
                await slack_call(token, "chat.postEphemeral", {
                    "channel": dm_channel, "user": approver_id,
                    "text": "Only the routed approver (a manager) can act on this request.",
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
                **decided_dm_blocks(d, approved=approved, approver_name=approver_name),
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
                             card=outcome_blocks(d, approved=approved, approver_name=approver_name))
