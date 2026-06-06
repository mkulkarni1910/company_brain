"""Policy-as-code domain models for the Guardrail engine.

Governance is enforced in code, outside the model. A ``Policy`` is a declarative,
versioned, owned object decoupled from any playbook. ``PolicyEngine.evaluate``
turns a Policy + typed facts into a deterministic ``Decision`` — never an LLM.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# allow         → proceed straight to act
# require_approval → pause for a governed human decision
# deny          → forbidden outright
# stop          → fail closed (missing/ambiguous data); the engine must not guess
PolicyResult = Literal["allow", "require_approval", "deny", "stop"]

ConditionOp = Literal["<=", ">=", "<", ">", "==", "!=", "in"]

_NUMERIC_OPS = {"<=", ">=", "<", ">"}
_CONTAINER_OPS = {"in"}


class Condition(BaseModel):
    """A single structured predicate over one typed fact.

    Value/op compatibility is validated at load time so a mis-authored policy
    fails loudly when PolicyStore.load() validates it — never as a runtime 500.
    """

    fact: str
    op: ConditionOp
    value: object

    @model_validator(mode="after")
    def _check_value_type(self) -> "Condition":
        if self.op in _NUMERIC_OPS and not isinstance(self.value, int | float):
            raise ValueError(
                f"condition on {self.fact!r}: operator {self.op!r} needs a numeric value, "
                f"got {type(self.value).__name__} ({self.value!r})"
            )
        if self.op in _CONTAINER_OPS and not isinstance(self.value, list | tuple | set):
            raise ValueError(
                f"condition on {self.fact!r}: operator 'in' needs a list value, "
                f"got {type(self.value).__name__} ({self.value!r})"
            )
        return self


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
