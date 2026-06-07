"""Generic request-approval playbook over Slack.

When → Check → Stop → Do → Record: a user asks to route something for sign-off,
SubstrateOS resolves the approver (the requester's manager from the Entra `manages`
edge, or stops), DMs them an Approve/Reject card, and records
every step. Nothing acts until a human decides. Lifts the RefundFlow pattern but
drops the refund-specific policy engine — the whole point is human sign-off.
"""

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
from app.bots.approval_cards import (
    approval_dm_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call, slack_get
from app.config import get_settings
from app.domain.audit import Actor
from app.domain.identity import User
from app.people.graph_client import PeopleGraphClient
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)


class ApprovalFlow:
    """Drives the approval playbook: ack → resolve approver → route → decide."""

    def __init__(
        self,
        *,
        store: RunStore,
        people: PeopleGraphClient | None,
        approval_service: ApprovalService | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self._store = store
        self._people = people
        self._audit = audit_log or AuditLog()
        self._approvals = approval_service or ApprovalService(
            store=ApprovalStore(), audit=self._audit
        )

    # ── Slack helpers ──────────────────────────────────────────────────────────

    async def _display_name(self, token: str, slack_user_id: str | None) -> str | None:
        if not slack_user_id:
            return None
        body = await slack_call(token, "users.info", {"user": slack_user_id})
        if not body:
            return None
        u = body.get("user") or {}
        profile = u.get("profile") or {}
        return profile.get("display_name") or u.get("real_name") or u.get("name")

    async def _email(self, token: str, slack_user_id: str | None) -> str | None:
        if not slack_user_id:
            return None
        body = await slack_call(token, "users.info", {"user": slack_user_id})
        return ((body or {}).get("user") or {}).get("profile", {}).get("email")

    async def _slack_id_for_email(self, token: str, email: str | None) -> str | None:
        if not email:
            return None
        # GET — Slack rejects JSON POST bodies for users.lookupByEmail.
        body = await slack_get(token, "users.lookupByEmail", {"email": email})
        return ((body or {}).get("user") or {}).get("id")

    async def _post(self, token: str, channel: str, thread_ts: str | None,
                    *, text: str, card: dict | None = None) -> dict | None:
        payload: dict = {"channel": channel, "text": text}
        if card:
            payload.update(card)
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await slack_call(token, "chat.postMessage", payload)

    # ── approver resolution (manager or stop) ─────────────────────────────────

    async def _resolve_approver(self, token: str, requester_slack_id: str | None,
                                tenant_id: str) -> tuple[str | None, str | None, str]:
        """Returns (approver_slack_id, approver_name, source). Source is
        'manager' or 'none' — there is no fallback approver: the playbook
        stops rather than guessing who may sign off."""
        if self._people is not None:
            email = await self._email(token, requester_slack_id)
            if email:
                mgr = await self._people.manager_of(email=email, tenant_id=tenant_id)
                if mgr:
                    sid = await self._slack_id_for_email(token, mgr.get("email"))
                    if sid:
                        return sid, mgr.get("display_name") or "your manager", "manager"
        return None, None, "none"

    # ── inbound request ────────────────────────────────────────────────────────

    async def handle_request(self, *, text: str, channel: str, thread_ts: str | None,
                             requester_slack_id: str | None, user: User) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        tenant = s.substrateos_tenant_id
        requester = await self._display_name(token, requester_slack_id) or "A teammate"
        requester_email = await self._email(token, requester_slack_id)
        run = await self._store.create(
            requester_name=requester, requester_slack_id=requester_slack_id,
            channel=channel, thread_ts=thread_ts, kind="approval", request_text=text,
        )
        await self._store.add_event(run.id, step="Request received",
                                    detail=f"{text[:160]} · from Slack", actor=requester)
        await self._audit.record(
            run_id=run.id, step="Request received",
            actor=Actor(type="human", id=requester_email or requester),
            inputs_summary=text[:160], surface="slack",
        )

        approver_id, approver_name, source = await self._resolve_approver(token, requester_slack_id, tenant)
        first = requester.split()[0] if requester != "A teammate" else "there"

        if not approver_id:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="No approver",
                                        detail="Couldn't resolve a manager to approve this", actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=(f"Hmm, {first} — I couldn't work out who should approve this. "
                                   "Ask an admin to set your manager in the directory and I'll route it."))
            return

        run.status = "pending_approval"
        run.approver_name = approver_name
        run.approver_slack_id = approver_id
        run.approver_source = source
        # Register a durable governed pending approval before saving the run.
        run.approval_id = await self._approvals.request(
            run_id=run.id, step="approve", required_role="manager",
            decision_context={"request": text[:200]},
        )
        await self._store.save(run)
        role = "requester's manager"
        await self._store.add_event(
            run.id, step="Approver resolved",
            detail=f"{approver_name} — {role}",
            actor="SubstrateOS",
        )

        await self._post(token, channel, thread_ts,
                         text=f"Sending that to {approver_name} for sign-off, {first} — I'll update here.",
                         card=needs_approval_blocks(request_text=text, approver_label=approver_name, run_id=run.id))

        opened = await slack_call(token, "conversations.open", {"users": approver_id})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if not dm:
            await self._post(token, channel, thread_ts,
                             text=f"I couldn't reach {approver_name} in a DM — please review manually.")
            return
        posted = await slack_call(token, "chat.postMessage", {
            "channel": dm, "text": "Approval needed",
            **approval_dm_blocks(request_text=text, requester_name=requester, run_id=run.id),
        })
        if posted:
            run.dm_channel = dm
            run.dm_ts = posted.get("ts")
            await self._store.save(run)
        await self._store.add_event(run.id, step="Routed for approval",
                                    detail=f"Approve/Reject card sent to {approver_name} in Slack", actor="SubstrateOS")

    # ── button clicks ──────────────────────────────────────────────────────────

    async def handle_action(self, payload: dict) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        actions = payload.get("actions") or []
        if not actions:
            return
        action_id = actions[0].get("action_id")
        if action_id not in ("approval_approve", "approval_reject"):
            return
        run_id = actions[0].get("value") or ""
        run = await self._store.get(run_id)
        if run is None or run.kind != "approval":
            logger.warning("approval action for unknown/mismatched run %r", run_id)
            return

        approver_id = (payload.get("user") or {}).get("id")
        container = payload.get("container") or {}
        dm_channel = run.dm_channel or container.get("channel_id")
        dm_ts = run.dm_ts or container.get("message_ts")
        req = run.request_text or "the request"

        if run.status != "pending_approval":
            if dm_channel and dm_ts:
                await slack_call(token, "chat.update", {
                    "channel": dm_channel, "ts": dm_ts, "text": f"Approval {run.status}",
                    **decided_dm_blocks(request_text=req,
                                        approved=(run.status in ("approved", "completed")),
                                        approver_name=run.approver_name or "a manager"),
                })
            return

        approved = action_id == "approval_approve"
        approver_name = (await self._display_name(token, approver_id)
                         or (payload.get("user") or {}).get("name") or "Manager")

        # Governed resolution: only the routed approver may act.
        # Enforce via ApprovalService if a durable pending exists.
        if run.approval_id:
            pending = await self._approvals.get_pending(run.approval_id)
            if pending is not None and pending.status == "pending":
                # Check this clicker is the routed approver (slack id match).
                is_routed = (run.approver_slack_id is not None
                             and approver_id == run.approver_slack_id)
                if not is_routed:
                    await self._store.add_event(
                        run.id, step="Approval denied",
                        detail=f"{approver_name} tried to act but is not the routed approver",
                        actor=approver_name,
                    )
                    if dm_channel and approver_id:
                        await slack_call(token, "chat.postEphemeral", {
                            "channel": dm_channel, "user": approver_id,
                            "text": "Only the routed approver can act on this request.",
                        })
                    return
                # Build a minimal User identity granting the manager role.
                approver_email = await self._email(token, approver_id) or f"{approver_id}@slack"
                identity = User(
                    user_id=approver_email,
                    tenant_id=s.substrateos_tenant_id,
                    email=approver_email,
                    display_name=approver_name,
                    group_ids={"manager"},
                )
                try:
                    await self._approvals.resolve(
                        run.approval_id, "approve" if approved else "reject", identity,
                        idp=None,
                    )
                except NotAuthorized:
                    await self._store.add_event(
                        run.id, step="Approval blocked",
                        detail=f"{approver_name} is not in role {pending.required_role}",
                        actor="SubstrateOS",
                    )
                    if dm_channel and approver_id:
                        await slack_call(token, "chat.postEphemeral", {
                            "channel": dm_channel, "user": approver_id,
                            "text": "You are not authorized to approve this request.",
                        })
                    return
                except (AlreadyResolved, UnknownApproval):
                    pass  # already decided — proceed idempotently
        else:
            # legacy run with no durable approval: emit typed audit manually
            await self._audit.record(
                run_id=run.id, step="Approved" if approved else "Rejected",
                actor=Actor(type="human", id=approver_id or approver_name),
                decision="approve" if approved else "reject",
                detail=f"{approver_name} {'approved' if approved else 'rejected'}",
            )

        run.status = "approved" if approved else "rejected"
        run.approver_name = approver_name
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Approved" if approved else "Rejected",
            detail=f"{approver_name} {'approved' if approved else 'rejected'} the request",
            actor=approver_name,
        )
        if dm_channel and dm_ts:
            await slack_call(token, "chat.update", {
                "channel": dm_channel, "ts": dm_ts,
                "text": f"Approval {'approved' if approved else 'rejected'}",
                **decided_dm_blocks(request_text=req, approved=approved, approver_name=approver_name),
            })
        if run.channel:
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Approval {'approved' if approved else 'rejected'} by {approver_name}",
                             card=outcome_blocks(request_text=req, approved=approved, approver_name=approver_name))
