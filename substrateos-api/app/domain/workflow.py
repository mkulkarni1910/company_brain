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


class RefundRun(BaseModel):
    """State of one refund workflow run (RB-xxxx)."""
    id: str
    status: RunStatus = "running"
    requester_name: str
    requester_slack_id: str | None = None
    channel: str | None = None
    thread_ts: str | None = None
    dm_channel: str | None = None
    dm_ts: str | None = None
    decision: RefundDecision | None = None
    approver_name: str | None = None
    created_at: datetime
    updated_at: datetime
