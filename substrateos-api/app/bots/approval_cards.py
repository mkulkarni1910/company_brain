"""Slack cards for the generic request-approval playbook.

Same shape as refund_cards: each builder returns a {"blocks", "attachments"}
fragment the flow spreads into chat.postMessage / chat.update. The colored
left-bar (amber routing / green approved / red rejected) comes from a message
attachment `color`.
"""

from __future__ import annotations

_AMBER = "#c8860d"
_GREEN = "#2f8f5b"
_RED = "#c8546a"


def _bar(color: str, blocks: list[dict]) -> dict:
    return {"color": color, "blocks": blocks}


def needs_approval_blocks(*, request_text: str, approver_label: str, run_id: str) -> dict:
    """Posted in the requester's channel — routed, awaiting sign-off."""
    return {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f":lock: *Routed for approval.*  Sent to *{approver_label}* for sign-off. I'll update here."}}],
        "attachments": [_bar(_AMBER, [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Request*\n{request_text[:600]}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"run {run_id}"}]},
        ])],
    }


def approval_dm_blocks(*, request_text: str, requester_name: str, run_id: str) -> dict:
    """The Approve/Reject card DM'd to the approver."""
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Approval needed"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Request*\n{request_text[:600]}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Requested by*\n{requester_name}"}},
        ],
        "attachments": [_bar(_AMBER, [
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": f":lock: nothing acts until you decide · run {run_id}"}]},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "approval_approve",
                 "value": run_id, "text": {"type": "plain_text", "text": "Approve"}},
                {"type": "button", "style": "danger", "action_id": "approval_reject",
                 "value": run_id, "text": {"type": "plain_text", "text": "Reject"}},
            ]},
        ])],
    }


def decided_dm_blocks(*, request_text: str, approved: bool, approver_name: str) -> dict:
    verdict = "Approved" if approved else "Rejected"
    icon = ":white_check_mark:" if approved else ":x:"
    return {"attachments": [_bar(_GREEN if approved else _RED, [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"{icon} *{verdict} by {approver_name}*\n{request_text[:400]}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded with the decision"}]},
    ])]}


def outcome_blocks(*, request_text: str, approved: bool, approver_name: str) -> dict:
    if approved:
        head = f":white_check_mark: *Approved by {approver_name}*"
        body = "You're clear to proceed."
    else:
        head = f":x: *Rejected by {approver_name}*"
        body = "No action was taken."
    return {"attachments": [_bar(_GREEN if approved else _RED, [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{head}\n{body}\n_{request_text[:300]}_"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded with the decision"}]},
    ])]}
