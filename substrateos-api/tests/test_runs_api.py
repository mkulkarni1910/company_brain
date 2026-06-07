from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.audit.log import AuditLog
from app.deps import get_audit_log, get_run_store
from app.domain.audit import Actor
from app.domain.workflow import RefundDecision
from app.main import app
from app.workflows.store import RunStore

_DEBUG = {"x-debug-bypass-auth": "t-test,u-tom,t-test:everyone"}


@pytest.fixture
def store():
    s = RunStore(client=None, force_memory=True)
    app.dependency_overrides[get_run_store] = lambda: s
    yield s
    app.dependency_overrides.clear()


@pytest.fixture
def store_with_audit():
    s = RunStore(client=None, force_memory=True)
    al = AuditLog(force_memory=True)
    app.dependency_overrides[get_run_store] = lambda: s
    app.dependency_overrides[get_audit_log] = lambda: al
    yield s, al
    app.dependency_overrides.clear()


def _seed(store: RunStore):
    async def _do():
        run = await store.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                                 channel="C", thread_ts=None)
        run.decision = RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                                      amount_usd=1200, order_age_days=45, policy_limit_usd=500,
                                      policy_limit_days=30, auto_approve=False, reasoning="over limit")
        run.status = "pending_approval"
        await store.save(run)
        await store.add_event(run.id, step="Request received", detail="d", actor="Tom Reyes")
        return run
    return asyncio.run(_do())


def test_list_runs(store):
    run = _seed(store)
    with TestClient(app) as client:
        resp = client.get("/runs", headers=_DEBUG)
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == run.id
    assert body[0]["status"] == "pending_approval"
    assert body[0]["decision"]["customer"] == "Priya Sharma"


def test_get_run_with_events(store):
    run = _seed(store)
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.id}", headers=_DEBUG)
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["id"] == run.id
    assert body["events"][0]["step"] == "Request received"


def test_get_run_404(store):
    with TestClient(app) as client:
        resp = client.get("/runs/RB-0000", headers=_DEBUG)
    assert resp.status_code == 404


def test_runs_require_auth(store):
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code in (401, 403)


def test_get_run_includes_typed_audit_trail(store_with_audit):
    s, al = store_with_audit

    def _do():
        async def _inner():
            run = await s.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                                 channel="C", thread_ts=None)
            await al.record(
                run_id=run.id,
                step="Rule evaluated",
                actor=Actor.agent("refund.v1@v1"),
                rule={"id": "refund.v1", "version": 1, "result": "allow"},
                decision="allow",
            )
            return run.id
        return asyncio.run(_inner())

    run_id = _do()

    with TestClient(app) as client:
        resp = client.get(f"/runs/{run_id}", headers=_DEBUG)

    assert resp.status_code == 200
    body = resp.json()
    assert "audit" in body
    rule_events = [e for e in body["audit"] if e.get("rule")]
    assert rule_events and rule_events[0]["rule"]["version"] == 1
    assert rule_events[0]["actor"]["type"] == "agent"
