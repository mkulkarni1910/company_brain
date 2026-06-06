"""Workstream 3 — Audit / provenance tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.audit.log import AuditLog
from app.domain.audit import Actor, AuditEvent


def _log() -> AuditLog:
    return AuditLog(client=None, force_memory=True)


@pytest.mark.asyncio
async def test_record_and_query_roundtrip_ordered():
    log = _log()
    await log.record(run_id="RB-1", step="Request received", actor=Actor.system())
    await log.record(
        run_id="RB-1",
        step="Rule evaluated",
        actor=Actor.agent("refund.v1@v1"),
        rule={"id": "refund.v1", "version": 1, "result": "require_approval"},
        decision="require_approval",
    )
    trail = await log.query("RB-1")
    assert [e.step for e in trail] == ["Request received", "Rule evaluated"]
    assert trail[1].rule == {"id": "refund.v1", "version": 1, "result": "require_approval"}
    assert trail[1].actor.type == "agent"


@pytest.mark.asyncio
async def test_actor_identity_is_real_for_humans():
    log = _log()
    await log.record(
        run_id="RB-2",
        step="Approved",
        actor=Actor(type="human", id="diana@acme.com", idp="entra"),
        decision="approve",
    )
    trail = await log.query("RB-2")
    assert trail[0].actor.type == "human"
    assert trail[0].actor.id == "diana@acme.com"
    assert trail[0].actor.idp == "entra"


@pytest.mark.asyncio
async def test_one_run_id_reconstructs_only_its_own_trail():
    log = _log()
    await log.record(run_id="RB-3", step="a", actor=Actor.system())
    await log.record(run_id="RB-4", step="b", actor=Actor.system())
    assert len(await log.query("RB-3")) == 1
    assert len(await log.query("RB-4")) == 1
    assert await log.query("RB-unknown") == []


def test_audit_event_is_immutable():
    e = AuditEvent.model_validate(
        {"ts": "2026-06-06T00:00:00Z", "run_id": "RB-1", "step": "x",
         "actor": {"type": "system", "id": "SubstrateOS"}}
    )
    with pytest.raises(ValidationError):
        e.step = "tampered"  # frozen — append-only, no mutation
