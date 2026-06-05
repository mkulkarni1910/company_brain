from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.workflows.store import RunStore


def _make_redis() -> MagicMock:
    """Mock async Redis supporting the string/list/seq ops RunStore uses."""
    r = MagicMock()
    kv: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    seq = {"n": 4470}

    async def set_(key, value):
        kv[key] = value

    async def get(key):
        return kv.get(key)

    async def incr(key):
        seq["n"] += 1
        return seq["n"]

    async def rpush(key, value):
        lists.setdefault(key, []).append(value)

    async def lpush(key, value):
        lists.setdefault(key, []).insert(0, value)

    async def lrange(key, start, end):
        items = lists.get(key, [])
        return items if end == -1 else items[start:end + 1]

    r.set = set_
    r.get = get
    r.incr = incr
    r.rpush = rpush
    r.lpush = lpush
    r.lrange = lrange
    return r


@pytest.mark.asyncio
async def test_create_assigns_sequential_rb_id():
    store = RunStore(client=_make_redis())
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U1",
                             channel="C1", thread_ts="123.45")
    assert run.id == "RB-4471"
    assert run.status == "running"
    run2 = await store.create(requester_name="Tom Reyes", requester_slack_id="U1",
                              channel="C1", thread_ts="123.46")
    assert run2.id == "RB-4472"


@pytest.mark.asyncio
async def test_save_and_get_roundtrip():
    store = RunStore(client=_make_redis())
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C1", thread_ts=None)
    run.status = "pending_approval"
    run.approver_name = "Diana Foster"
    await store.save(run)
    loaded = await store.get(run.id)
    assert loaded is not None
    assert loaded.status == "pending_approval"
    assert loaded.approver_name == "Diana Foster"


@pytest.mark.asyncio
async def test_get_unknown_returns_none():
    store = RunStore(client=_make_redis())
    assert await store.get("RB-9999") is None


@pytest.mark.asyncio
async def test_events_append_and_list_in_order():
    store = RunStore(client=_make_redis())
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C1", thread_ts=None)
    await store.add_event(run.id, step="Request received", detail="Refund $1,200", actor="Tom Reyes")
    await store.add_event(run.id, step="Facts gathered", detail="Order #48213", actor="SubStrateOS")
    events = await store.list_events(run.id)
    assert [e.step for e in events] == ["Request received", "Facts gathered"]
    assert events[0].actor == "Tom Reyes"


@pytest.mark.asyncio
async def test_list_runs_newest_first():
    store = RunStore(client=_make_redis())
    r1 = await store.create(requester_name="Tom", requester_slack_id=None, channel="C1", thread_ts=None)
    r2 = await store.create(requester_name="Tom", requester_slack_id=None, channel="C1", thread_ts=None)
    runs = await store.list_runs()
    assert [r.id for r in runs] == [r2.id, r1.id]


@pytest.mark.asyncio
async def test_memory_fallback_without_redis():
    store = RunStore(client=None, force_memory=True)
    run = await store.create(requester_name="Tom", requester_slack_id=None, channel="C1", thread_ts=None)
    assert run.id == "RB-4471"
    await store.add_event(run.id, step="Request received", detail="d", actor="Tom")
    assert (await store.get(run.id)) is not None
    assert len(await store.list_events(run.id)) == 1
