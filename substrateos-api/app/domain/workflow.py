from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RunStatus = Literal[
    "running", "pending_approval", "approved", "rejected", "denied", "halted", "completed", "error"
]


class RefundFacts(BaseModel):
    """Typed facts the model EXTRACTS from the request + grounded order context.

    The model produces facts only — it never decides the outcome. The deterministic
    PolicyEngine (app/policy) decides allow/require_approval over these facts.
    """
    found: bool = False
    order_id: str | None = None
    customer: str | None = None
    amount_usd: float | None = None
    order_age_days: int | None = None
    reasoning: str = ""


class RefundDecision(BaseModel):
    """Render view for the Slack cards + run record. Populated by the flow from the
    extracted RefundFacts plus the evaluated policy — NOT directly by the model
    (``policy_limit_*`` and ``auto_approve`` now come from the guardrail, in code)."""
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
    approval_id: str | None = None  # the durable PendingApproval gating this run
    created_at: datetime
    updated_at: datetime
