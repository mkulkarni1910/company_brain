"""Human-in-the-loop approval domain models.

When the guardrail returns ``require_approval`` the run durably pauses as a
``PendingApproval`` and resumes only on a governed, identity-bound decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.identity import User

ApprovalChoice = Literal["approve", "reject"]
ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class PendingApproval(BaseModel):
    """Persisted pause point — survives restarts; the run waits on this."""

    id: str
    run_id: str
    step: str
    required_role: str
    decision_context: dict = Field(default_factory=dict)
    rule_id: str | None = None
    rule_version: int | None = None
    created_at: datetime
    status: ApprovalStatus = "pending"


class ApprovalDecision(BaseModel):
    """The governed human decision. Carries a REAL identity (Block 4)."""

    approval_id: str
    choice: ApprovalChoice
    approver: User
    note: str | None = None
    decided_at: datetime
