from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RunStatus = Literal["running", "pending_approval", "approved", "rejected", "completed", "error"]


class RefundDecision(BaseModel):
    """Structured output of the refund engine's single LLM call."""
    found: bool = False
    order_id: str | None = None
    customer: str | None = None
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


RunKind = Literal["refund", "approval"]


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
    dm_channel: str | None = None
    dm_ts: str | None = None
    decision: RefundDecision | None = None
    approver_name: str | None = None
    # generic approval playbook
    request_text: str | None = None
    approver_source: str | None = None  # "manager" | "fallback" | "mention"
    created_at: datetime
    updated_at: datetime
