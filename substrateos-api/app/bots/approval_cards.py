"""Block Kit cards for the generic request-approval playbook (Slack)."""

from __future__ import annotations


def _request_section(request_text: str, requester_name: str, run_id: str) -> dict:
    return {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Request*\n{request_text[:600]}"},
            {"type": "mrkdwn", "text": f"*Requested by*\n{requester_name}"},
        ],
    }


def approval_dm_blocks(*, request_text: str, requester_name: str, run_id: str) -> list[dict]:
    """The Approve/Reject card DM'd to the approver (manager)."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Approval needed"}},
        _request_section(request_text, requester_name, run_id),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"run {run_id} · nothing acts until you decide"}]},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"},
             "action_id": "approval_approve", "value": run_id},
            {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Reject"},
             "action_id": "approval_reject", "value": run_id},
        ]},
    ]


def needs_approval_blocks(*, request_text: str, approver_label: str, run_id: str) -> list[dict]:
    """Posted back in the requester's channel — routed, awaiting sign-off."""
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":lock: *Routed for approval.*  Sent to *{approver_label}* for sign-off. I'll update here."}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Request:* {request_text[:600]}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"run {run_id}"}]},
    ]


def decided_dm_blocks(*, request_text: str, approved: bool, approver_name: str) -> list[dict]:
    """Re-render of the approver's DM after they decide (buttons removed)."""
    verb = "Approved" if approved else "Rejected"
    icon = ":white_check_mark:" if approved else ":x:"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": f"{verb}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{icon} *{verb} by {approver_name}.*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Request:* {request_text[:600]}"}},
    ]


def outcome_blocks(*, request_text: str, approved: bool, approver_name: str) -> list[dict]:
    """Posted to the requester's channel once the approver decides."""
    if approved:
        text = f":white_check_mark: *Approved by {approver_name}.* You're clear to proceed."
    else:
        text = f":x: *Rejected by {approver_name}.* No action was taken."
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Request:* {request_text[:600]}"}},
    ]
