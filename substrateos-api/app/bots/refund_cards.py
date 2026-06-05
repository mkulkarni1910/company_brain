from __future__ import annotations

from app.domain.workflow import RefundDecision


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


def needs_approval_blocks(d: RefundDecision, *, approver_label: str, run_id: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":warning: *I can't auto-approve this one.*  `Needs approval` · run {run_id}"}},
        _facts_fields(d),
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Why:* {d.reasoning}\n*What I'm doing:* Routed to *{approver_label}* for approval. I'll update here."}},
    ]


def auto_approved_blocks(d: RefundDecision, *, run_id: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":white_check_mark: *Auto-approved within policy.* · run {run_id}"}},
        _facts_fields(d),
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Why:* {d.reasoning}\n*Done:* Refund of {_usd(d.amount_usd)} issued to "
                 f"{d.customer} on order #{d.order_id}. Recorded in the audit log."}},
    ]


def approval_dm_blocks(d: RefundDecision, *, requester_name: str, run_id: str) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Refund needs your approval"}},
        _facts_fields(d),
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Requested by:* {requester_name}\n*Reason:* {d.reasoning}\n_run {run_id}_"}},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary", "action_id": "refund_approve",
             "value": run_id, "text": {"type": "plain_text", "text": "Approve"}},
            {"type": "button", "style": "danger", "action_id": "refund_reject",
             "value": run_id, "text": {"type": "plain_text", "text": "Reject"}},
        ]},
    ]


def decided_dm_blocks(d: RefundDecision, *, approved: bool, approver_name: str) -> list[dict]:
    verdict = "Approved" if approved else "Rejected"
    icon = ":white_check_mark:" if approved else ":x:"
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"{icon} *{verdict} by {approver_name}*\nRefund of {_usd(d.amount_usd)} on "
                 f"order #{d.order_id} · decision recorded in the audit log."}},
    ]


def outcome_blocks(d: RefundDecision, *, approved: bool, approver_name: str) -> list[dict]:
    if approved:
        text = (f":white_check_mark: *Approved* by *{approver_name}*.\nRefund of "
                f"{_usd(d.amount_usd)} issued to {d.customer} on order #{d.order_id}. "
                "Confirmation sent to the customer; the full record is in the audit log.")
    else:
        text = (f":x: *Rejected* by *{approver_name}*.\nThe refund of {_usd(d.amount_usd)} on "
                f"order #{d.order_id} was declined. The decision is recorded in the audit log.")
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
