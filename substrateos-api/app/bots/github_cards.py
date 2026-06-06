"""Cards for the raise-PR playbook: Slack blocks (colored left-bar, same shape
as approval_cards) + the Teams Adaptive Card preview."""

from __future__ import annotations

from app.domain.workflow import PrDraft

_AMBER = "#c8860d"
_GREEN = "#2f8f5b"
_RED = "#c8546a"


def _bar(color: str, blocks: list[dict]) -> dict:
    return {"color": color, "blocks": blocks}


# ── Slack ──────────────────────────────────────────────────────────────────────

def preview_blocks(*, draft: PrDraft, repo_label: str, run_id: str) -> dict:
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "PR drafted — confirm to create"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{draft.title}*\n{draft.summary}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"`{repo_label}` · `{draft.path}`"}},
        ],
        "attachments": [_bar(_AMBER, [
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": f":lock: nothing reaches GitHub until you confirm — the PR will be authored as you · run {run_id}"}]},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "github_create",
                 "value": run_id, "text": {"type": "plain_text", "text": "Create PR"}},
                {"type": "button", "style": "danger", "action_id": "github_cancel",
                 "value": run_id, "text": {"type": "plain_text", "text": "Cancel"}},
            ]},
        ])],
    }


def pr_created_blocks(*, pr_url: str, title: str, actor_name: str) -> dict:
    return {"attachments": [_bar(_GREEN, [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f":white_check_mark: *PR created* — <{pr_url}|{title}>\nConfirmed by {actor_name}; authored as them on GitHub."}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded in the audit log"}]},
    ])]}


def cancelled_blocks(*, title: str, actor_name: str) -> dict:
    return {"attachments": [_bar(_RED, [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f":x: *Cancelled by {actor_name}* — _{title}_\nNothing reached GitHub."}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded with the decision"}]},
    ])]}


# ── Teams (Adaptive Card) ──────────────────────────────────────────────────────

def teams_preview_activity(*, draft: PrDraft, repo_label: str, run_id: str) -> dict:
    card = {
        "type": "AdaptiveCard", "version": "1.5",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [
            {"type": "TextBlock", "weight": "Bolder", "size": "Medium",
             "text": "PR drafted — confirm to create"},
            {"type": "TextBlock", "wrap": True, "text": f"**{draft.title}** — {draft.summary}"},
            {"type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "Small",
             "text": f"{repo_label} · {draft.path}"},
            {"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small",
             "text": f"\U0001f512 Nothing reaches GitHub until you confirm — the PR will be authored as you · run {run_id}"},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Create PR",
             "data": {"action": "github_create", "run_id": run_id}},
            {"type": "Action.Submit", "title": "Cancel",
             "data": {"action": "github_cancel", "run_id": run_id}},
        ],
    }
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}
