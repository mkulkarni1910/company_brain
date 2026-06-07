"""find_routed_run: links a decided refund back to the customer's hand-off run."""

from __future__ import annotations

import pytest

from app.domain.workflow import RefundDecision
from app.workflows.store import RunStore


def _decision(order_id: str) -> RefundDecision:
    return RefundDecision(found=True, order_id=order_id, customer="Priya Sharma",
                          amount_usd=1200, auto_approve=False, reasoning="r")


async def _routed(store, order_id: str, status: str = "routed_to_support",
                  kind: str = "refund"):
    run = await store.create(requester_name="Priya Sharma", requester_slack_id="U_PRIYA",
                             channel="D_PRIYA", thread_ts="50.1", kind=kind)
    run.decision = _decision(order_id)
    run.status = status
    await store.save(run)
    return run


@pytest.mark.asyncio
async def test_finds_matching_routed_run():
    store = RunStore(client=None, force_memory=True)
    run = await _routed(store, "48213")
    found = await store.find_routed_run("48213")
    assert found is not None and found.id == run.id


@pytest.mark.asyncio
async def test_latest_match_wins():
    store = RunStore(client=None, force_memory=True)
    await _routed(store, "48213")
    newer = await _routed(store, "48213")
    assert (await store.find_routed_run("48213")).id == newer.id


@pytest.mark.asyncio
async def test_filters_status_kind_order_and_none():
    store = RunStore(client=None, force_memory=True)
    await _routed(store, "48213", status="completed")        # already resolved
    await _routed(store, "99999")                            # different order
    await _routed(store, "48213", kind="approval")           # different kind
    assert (await store.find_routed_run("48213")) is None
    assert (await store.find_routed_run(None)) is None
    assert (await store.find_routed_run("48213")) is None
