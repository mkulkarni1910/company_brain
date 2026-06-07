from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.domain.skill import SkillCreate

RunStatus = Literal[
    "running", "pending_approval", "pending_confirm",
    "approved", "rejected", "completed", "cancelled", "error",
    "needs_attention",    # stopped: no eligible approver / identity unknown
    "routed_to_support",  # customer request handed to the support channel
]


class RefundDecision(BaseModel):
    """Structured output of the refund engine's single LLM call."""
    found: bool = False
    order_id: str | None = None
    customer: str | None = None
    customer_email: str | None = None  # from the order record — powers the outcome DM fallback
    amount_usd: float | None = None
    order_age_days: int | None = None
    policy_limit_usd: float | None = None
    policy_limit_days: int | None = None
    auto_approve: bool = False
    reasoning: str = ""


class RunEvent(BaseModel):
    """One audit-trail entry for a workflow run."""
    ts: datetime
    step: str
    detail: str
    actor: str


RunKind = Literal["refund", "approval", "github_pr", "skill_publish"]


class PrDraft(BaseModel):
    """The AI-drafted change awaiting the requester's confirm (github_pr runs)."""
    path: str
    base_sha: str       # sha of the current file (Contents API requires it on update)
    new_content: str
    summary: str        # one-line, shown on the preview card
    title: str          # PR title
    body: str           # PR description (markdown)


class RefundRun(BaseModel):
    """State of one workflow run. Named for the original refund playbook, but now
    also backs the generic request-approval playbook (kind='approval')."""
    id: str
    kind: RunKind = "refund"
    status: RunStatus = "running"
    requester_name: str
    requester_slack_id: str | None = None
    channel: str | None = None
    thread_ts: str | None = None
    # support-channel hand-off card (customer routed runs) — resolved on outcome
    handoff_channel: str | None = None
    handoff_ts: str | None = None
    dm_channel: str | None = None
    dm_ts: str | None = None
    decision: RefundDecision | None = None
    approver_name: str | None = None
    approver_slack_id: str | None = None  # the routed approver — click enforcement key
    # generic approval playbook
    request_text: str | None = None
    approver_source: str | None = None  # "manager" | "mention"
    # github_pr playbook
    surface: str | None = None          # "web" | "slack" | "teams"
    requester_email: str | None = None  # identity key for the per-user GitHub token
    pr_draft: PrDraft | None = None
    pr_url: str | None = None
    # skill_publish playbook (SME Skill Studio): the AI-drafted skill awaiting
    # an admin's decision — the live SkillStore is only written on approval.
    skill_draft: SkillCreate | None = None
    rejection_note: str | None = None
    created_at: datetime
    updated_at: datetime
