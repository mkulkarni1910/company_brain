"""Slack cards for the refund playbook.

Each builder returns a message-payload fragment ({"blocks": [...], "attachments":
[...]}) that the flow spreads into chat.postMessage / chat.update. The colored
left-bar (amber for "needs approval", green for approved, red for rejected) comes
from a message *attachment* with a `color` — the one piece of Slack styling that
lets these read as cards rather than a flat mrkdwn dump.
"""

from __future__ import annotations

from app.domain.workflow import RefundDecision

_AMBER = "#c8860d"
_GREEN = "#2f8f5b"
_RED = "#c8546a"


def _usd(v: float | None) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _facts_fields(d: RefundDecision) -> dict:
    return {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Customer*\n{d.customer or '—'}"},
            {"type": "mrkdwn", "text": f"*Order*\n#{d.order_id or '—'}"},
            {"type": "mrkdwn", "text": f"*Amount*\n{_usd(d.amount_usd)}"},
            {"type": "mrkdwn", "text": f"*Age*\n{d.order_age_days} days"},
        ],
    }


def _bar(color: str, blocks: list[dict]) -> dict:
    return {"color": color, "blocks": blocks}


def needs_approval_blocks(d: RefundDecision, *, approver_label: str, run_id: str) -> dict:
    return {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f":warning: *I can't auto-approve this one.*  `Needs approval` · run {run_id}"}}],
        "attachments": [_bar(_AMBER, [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why*\n{d.reasoning}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*What I'm doing*\nRouted to *{approver_label}* for approval. I'll update here."}},
        ])],
    }


def auto_approved_blocks(d: RefundDecision, *, run_id: str) -> dict:
    return {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f":white_check_mark: *Auto-approved within policy.* · run {run_id}"}}],
        "attachments": [_bar(_GREEN, [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why*\n{d.reasoning}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*Done*\nRefund of {_usd(d.amount_usd)} issued to {d.customer} on order "
                        f"#{d.order_id}. Recorded in the audit log."}},
        ])],
    }


def approval_dm_blocks(d: RefundDecision, *, requester_name: str, run_id: str) -> dict:
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Refund needs your approval"}},
            _facts_fields(d),
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Requested by*\n{requester_name}"}},
        ],
        # callout + buttons in an amber attachment so the action zone reads as a card.
        "attachments": [_bar(_AMBER, [
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"Over the auto-approve limit (*{_usd(d.policy_limit_usd)} / "
                        f"{d.policy_limit_days} days*). Your approval is required before the refund is issued."}},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "refund_approve",
                 "value": run_id, "text": {"type": "plain_text", "text": "Approve"}},
                {"type": "button", "style": "danger", "action_id": "refund_reject",
                 "value": run_id, "text": {"type": "plain_text", "text": "Reject"}},
            ]},
        ])],
    }


def decided_dm_blocks(d: RefundDecision, *, approved: bool, approver_name: str) -> dict:
    verdict = "Approved" if approved else "Rejected"
    icon = ":white_check_mark:" if approved else ":x:"
    return {"attachments": [_bar(_GREEN if approved else _RED, [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"{icon} *{verdict} by {approver_name}*\nRefund of {_usd(d.amount_usd)} on order #{d.order_id}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: decision recorded in the audit log"}]},
    ])]}


def outcome_blocks(d: RefundDecision, *, approved: bool, approver_name: str,
                   mention: str | None = None) -> dict:
    verdict = "approved" if approved else "rejected"
    if approved:
        head = (f":white_check_mark: *Hello {mention} — refund {verdict} by {approver_name}*"
                if mention else f":white_check_mark: *Approved by {approver_name}*")
        body = f"Refund of {_usd(d.amount_usd)} issued to {d.customer} on order #{d.order_id}. Confirmation sent."
    else:
        head = (f":x: *Hello {mention} — refund {verdict} by {approver_name}*"
                if mention else f":x: *Rejected by {approver_name}*")
        body = f"The refund of {_usd(d.amount_usd)} on order #{d.order_id} was declined."
    return {"attachments": [_bar(_GREEN if approved else _RED, [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{head}\n{body}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded with the decision"}]},
    ])]}


def customer_outcome_blocks(d: RefundDecision, *, approved: bool) -> dict:
    """Customer-facing outcome — policy facts only, never internal mechanics
    (no managers, approvals, or exceptions in the REJECTED copy)."""
    first = (d.customer or "there").split()[0]
    if approved:
        text = (f"Hello {first} — good news! Your refund of {_usd(d.amount_usd)} for "
                f"order #{d.order_id} has been approved and is being processed.")
        color = _GREEN
    else:
        text = (f"Hello {first} — we couldn't process your refund for order "
                f"#{d.order_id}: it falls outside our refund policy "
                f"({_usd(d.policy_limit_usd)} within {d.policy_limit_days} days). "
                "Please reach out to our support team if you have questions.")
        color = _RED
    return {"attachments": [_bar(color, [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ])]}


def customer_request_blocks(*, request_text: str, customer_name: str, run_id: str,
                            decision: RefundDecision | None = None) -> dict:
    """Channel card for a customer's refund ask — needs a support agent to pick
    it up and run the playbook themselves (customers can't trigger refunds).
    When the engine pre-fetched their order, the facts ride along."""
    inner: list[dict] = []
    if decision is not None and decision.found:
        inner.append(_facts_fields(decision))
        inner.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": (f"Order fetched automatically for the requester — "
                     f"{'within' if decision.auto_approve else 'over'} "
                     "the auto-approve limit.")}]})
    inner.extend([
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*From*\n{customer_name}"},
            {"type": "mrkdwn", "text": f"*Request*\n{request_text[:500]}"},
        ]},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": "Customers can't trigger refunds directly — an agent should "
                    "pick this up and run it."}]},
    ])
    return {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f":wave: *Customer refund request* — needs a support agent · run {run_id}"}}],
        "attachments": [_bar(_AMBER, inner)],
    }
