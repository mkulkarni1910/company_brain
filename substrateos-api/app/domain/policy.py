"""Policy-as-code domain models for the Guardrail engine.

Governance is enforced in code, outside the model. A ``Policy`` is a declarative,
versioned, owned object decoupled from any playbook. ``PolicyEngine.evaluate``
turns a Policy + typed facts into a deterministic ``Decision`` — never an LLM.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# allow         → proceed straight to act
# require_approval → pause for a governed human decision
# deny          → forbidden outright
# stop          → fail closed (missing/ambiguous data); the engine must not guess
PolicyResult = Literal["allow", "require_approval", "deny", "stop"]

ConditionOp = Literal["<=", ">=", "<", ">", "==", "!=", "in"]


class Condition(BaseModel):
    """A single structured predicate over one typed fact."""

    fact: str
    op: ConditionOp
    value: object


class Policy(BaseModel):
    """Declarative, versioned guardrail. Referenced by id+version so changing a
    threshold in the file changes behavior everywhere with no code/prompt edit."""

    id: str
    version: int
    owner: str
    description: str = ""
    # ``all`` is an AND group. (any/ groups can be added later without breaking this.)
    all: list[Condition] = Field(default_factory=list)
    on_pass: PolicyResult = "allow"
    on_fail: PolicyResult = "require_approval"
    # role required to resolve when an outcome is require_approval
    required_role: str | None = None
    on_missing_data: PolicyResult = "stop"  # FAIL CLOSED


class Decision(BaseModel):
    """The deterministic output of evaluating a Policy over typed facts.

    Carries rule_id+rule_version and the evidence (facts) it decided on, so the
    audit trail can prove exactly which rule, at which version, produced it.
    """

    result: PolicyResult
    reason: str
    rule_id: str
    rule_version: int
    required_role: str | None = None
    evidence: dict = Field(default_factory=dict)
