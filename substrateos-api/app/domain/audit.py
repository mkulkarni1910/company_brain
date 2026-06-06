"""Audit / provenance domain models.

An append-only, identity-stamped record of every step — the receipt that proves
the AI never acted on its own. Actors are typed (human|system|agent) and carry a
real identity, so the trail can answer "who authorized this?" for every action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ActorType = Literal["human", "system", "agent"]


class Actor(BaseModel):
    """Who performed a step. Humans carry a real identity (idp='entra')."""

    type: ActorType
    id: str
    idp: str | None = None

    @classmethod
    def system(cls, id: str = "SubstrateOS") -> Actor:
        return cls(type="system", id=id)

    @classmethod
    def agent(cls, id: str) -> Actor:
        return cls(type="agent", id=id)


class AuditEvent(BaseModel):
    """One immutable, ordered step in a run's provenance, queryable by run_id."""

    ts: datetime
    run_id: str
    step: str
    actor: Actor
    action: str = ""
    inputs_summary: str | None = None
    rule: dict | None = None  # {id, version, result}
    decision: str | None = None
    target: dict | None = None  # {order_id?, refund_id?}
    before: dict | None = None
    after: dict | None = None
    surface: str | None = None
    detail: str | None = None  # human-readable line (back-compat with run timelines)

    model_config = {"frozen": True}  # immutable once created


class RunSummary(BaseModel):
    """Lightweight per-run rollup the read view can return alongside the trail."""

    run_id: str
    events: list[AuditEvent] = Field(default_factory=list)
