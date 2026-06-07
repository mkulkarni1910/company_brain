"""SME Skill Studio publish playbook: submit → manager sign-off → live skill.

When → Check → Stop → Do → Record over a web submission: a Finance SME submits
an AI-drafted skill from /studio, the draft is parked on a run (NEVER the live
skill store), the submitter's manager gets a Slack Approve/Reject card
(best-effort — the admin queue is the source of truth), and only a decision
writes the skill to the catalog. SMEs may resubmit (edit) or withdraw their own
pending/rejected submissions; withdrawal cancels the run but keeps the audit
trail. Mirrors ApprovalFlow, minus Slack-as-surface: the request arrives over
HTTP, so requester identity is the Entra User.
"""
from __future__ import annotations

import logging

from app.bots.approval_cards import decided_dm_blocks, skill_publish_dm_blocks
from app.bots.slack import slack_call, slack_get
from app.config import get_settings
from app.domain.identity import User
from app.domain.skill import SkillCreate
from app.domain.workflow import RefundRun
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)


class SlugConflictError(ValueError):
    """The draft's slug is already live or already pending."""


class AlreadyDecidedError(RuntimeError):
    """A second decision arrived after the run left pending_approval."""


class NotEditableError(RuntimeError):
    """The run isn't in an SME-editable state (only pending/rejected are)."""


class SkillPublishFlow:
    def __init__(self, *, store: RunStore, skill_store, people) -> None:
        self._store = store
        self._skills = skill_store
        self._people = people

    # ── submit ────────────────────────────────────────────────────────────────

    async def _check_slug_free(self, slug: str) -> None:
        if self._skills is not None and await self._skills.get_by_slug(slug) is not None:
            raise SlugConflictError(f"slug '{slug}' already exists in the catalog")
        # Window: only the latest 100 runs are scanned — a pending draft older
        # than that escapes the duplicate check (catalog check above still holds).
        for r in await self._store.list_runs(limit=100):
            if (r.kind == "skill_publish" and r.status == "pending_approval"
                    and r.skill_draft is not None and r.skill_draft.slug == slug):
                raise SlugConflictError(f"slug '{slug}' already has a pending submission")

    async def submit(self, *, draft: SkillCreate, source_text: str, user: User) -> RefundRun:
        await self._check_slug_free(draft.slug)
        run = await self._store.create(
            requester_name=user.display_name, requester_slack_id=None,
            channel=None, thread_ts=None, kind="skill_publish",
            request_text=source_text or None)
        run.requester_email = user.email
        run.skill_draft = draft
        run.status = "pending_approval"
        run.surface = "web"
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Skill submitted",
            detail=f"'{draft.name}' (/{draft.slug}) drafted from plain English in the Studio",
            actor=user.display_name)
        await self._route_to_manager(run, user)
        return run

    async def _route_to_manager(self, run: RefundRun, user: User) -> None:
        """Best-effort Slack card to the submitter's Entra manager. Failure to
        route never fails the submission — the admin queue is the source of truth."""
        s = get_settings()
        token = s.slack_bot_token or ""
        mgr: dict | None = None
        if self._people is not None and user.email:
            try:
                mgr = await self._people.manager_of(
                    email=user.email, tenant_id=s.substrateos_tenant_id)
            except Exception:  # noqa: BLE001 — best-effort
                mgr = None
        sid: str | None = None
        if token and mgr and mgr.get("email"):
            body = await slack_get(token, "users.lookupByEmail", {"email": mgr["email"]})
            sid = ((body or {}).get("user") or {}).get("id")
        if not sid:
            await self._store.add_event(
                run.id, step="Approver not resolved",
                detail="No manager reachable on Slack — review it in the admin queue",
                actor="SubstrateOS")
            return
        run.approver_name = mgr.get("display_name") or "your manager"
        run.approver_slack_id = sid
        run.approver_source = "manager"
        opened = await slack_call(token, "conversations.open", {"users": sid})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if dm:
            d = run.skill_draft
            posted = await slack_call(token, "chat.postMessage", {
                "channel": dm, "text": "New skill awaiting approval",
                **skill_publish_dm_blocks(
                    skill_name=d.name, slug=d.slug, description=d.description,
                    steps=d.steps, submitter_name=run.requester_name, run_id=run.id),
            })
            if posted:
                run.dm_channel = dm
                run.dm_ts = posted.get("ts")
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed for approval",
            detail=f"Approve/Reject card sent to {run.approver_name} in Slack",
            actor="SubstrateOS")

    # ── SME edits: resubmit / withdraw ───────────────────────────────────────

    async def _get_own(self, run_id: str, user: User) -> RefundRun:
        run = await self._store.get(run_id)
        if run is None or run.kind != "skill_publish" or run.skill_draft is None:
            raise KeyError(run_id)
        if run.requester_email != user.email:
            raise PermissionError(run_id)
        return run

    async def resubmit(self, *, run_id: str, draft: SkillCreate, source_text: str,
                       user: User) -> RefundRun:
        run = await self._get_own(run_id, user)
        # Resubmitting a *pending* run swaps the draft under the same run_id; a
        # manager racing the swap from the old card approves the new content.
        # Demo-grade window — the old card is invalidated below to shrink it.
        if run.status not in ("pending_approval", "rejected"):
            raise NotEditableError(run.status)
        if draft.slug != run.skill_draft.slug:
            await self._check_slug_free(draft.slug)  # unchanged slug would match itself
        old_card = (run.dm_channel, run.dm_ts)
        run.skill_draft = draft
        run.request_text = source_text or run.request_text
        run.status = "pending_approval"
        run.rejection_note = None
        run.dm_channel = None
        run.dm_ts = None
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Resubmitted",
            detail=f"'{draft.name}' (/{draft.slug}) edited and resubmitted",
            actor=user.display_name)
        await self._invalidate_card(old_card, reason="replaced by a resubmission")
        await self._route_to_manager(run, user)
        return run

    async def withdraw(self, *, run_id: str, user: User) -> RefundRun:
        run = await self._get_own(run_id, user)
        if run.status not in ("pending_approval", "rejected"):
            raise NotEditableError(run.status)
        run.status = "cancelled"
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Withdrawn",
            detail=f"'{run.skill_draft.name}' withdrawn by the submitter — kept in the audit log",
            actor=user.display_name)
        await self._invalidate_card((run.dm_channel, run.dm_ts),
                                    reason="withdrawn by the submitter")
        return run

    async def _invalidate_card(self, card: tuple[str | None, str | None], *, reason: str) -> None:
        token = get_settings().slack_bot_token or ""
        channel, ts = card
        if not (token and channel and ts):
            return
        await slack_call(token, "chat.update", {
            "channel": channel, "ts": ts,
            "text": f"No decision needed — {reason}.",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn",
                "text": f":leftwards_arrow_with_hook: *No decision needed* — {reason}."}}],
            "attachments": [],
        })

    # ── decide (single code path: admin queue AND Slack card) ────────────────

    async def decide(self, *, run_id: str, approve: bool, actor_name: str,
                     note: str | None = None) -> RefundRun:
        run = await self._store.get(run_id)
        if run is None or run.kind != "skill_publish" or run.skill_draft is None:
            raise KeyError(run_id)
        if run.status != "pending_approval":
            raise AlreadyDecidedError(run.status)
        if approve:
            # Deliberate ordering: skill goes live, then the run is saved. A crash
            # between the two leaves a live skill with a pending run; the retry
            # surfaces as ValueError below rather than silent duplication.
            # ValueError (slug landed while pending) propagates → API maps to 409.
            await self._skills.create(run.skill_draft)
            run.status = "approved"
        else:
            run.status = "rejected"
            run.rejection_note = (note or "").strip() or None
        run.approver_name = actor_name
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Approved" if approve else "Rejected",
            detail=(f"{actor_name} approved — '{run.skill_draft.name}' is live in the catalog"
                    if approve else
                    f"{actor_name} rejected: {run.rejection_note or 'no note'}"),
            actor=actor_name)
        await self._update_dm_card(run, approved=approve)
        return run

    async def _update_dm_card(self, run: RefundRun, *, approved: bool) -> None:
        token = get_settings().slack_bot_token or ""
        if not (token and run.dm_channel and run.dm_ts):
            return
        await slack_call(token, "chat.update", {
            "channel": run.dm_channel, "ts": run.dm_ts,
            "text": f"Skill {'approved' if approved else 'rejected'}",
            **decided_dm_blocks(
                request_text=f"Skill '{run.skill_draft.name}' (/{run.skill_draft.slug})",
                approved=approved, approver_name=run.approver_name or "an admin"),
        })

    # ── Slack button clicks ───────────────────────────────────────────────────

    async def handle_action(self, payload: dict) -> None:
        token = get_settings().slack_bot_token or ""
        actions = payload.get("actions") or []
        if not actions:
            return
        action_id = actions[0].get("action_id")
        if action_id not in ("skillpub_approve", "skillpub_reject"):
            return
        run_id = actions[0].get("value") or ""
        run = await self._store.get(run_id)
        if run is None or run.kind != "skill_publish":
            logger.warning("skillpub action for unknown/mismatched run %r", run_id)
            return
        clicker = (payload.get("user") or {}).get("id")
        # approver_slack_id None ⇒ no card was ever posted (approver unresolved),
        # so there is no button for an arbitrary clicker to reach.
        if run.approver_slack_id and clicker != run.approver_slack_id:
            logger.warning("skillpub click by %r ignored — routed approver is %r",
                           clicker, run.approver_slack_id)
            return
        body = await slack_call(token, "users.info", {"user": clicker}) if clicker else None
        u = (body or {}).get("user") or {}
        actor = (u.get("profile", {}).get("display_name")
                 or u.get("real_name")
                 or (payload.get("user") or {}).get("name") or "Manager")
        try:
            await self.decide(run_id=run_id, approve=(action_id == "skillpub_approve"),
                              actor_name=actor)
        except AlreadyDecidedError:
            fresh = await self._store.get(run_id)
            if fresh is not None:
                await self._update_dm_card(
                    fresh, approved=fresh.status in ("approved", "completed"))
        except ValueError as e:  # slug conflict at approval time
            if run.dm_channel:
                await slack_call(token, "chat.postMessage", {
                    "channel": run.dm_channel,
                    "text": f"Couldn't publish: {e}. Review it in the admin panel."})
