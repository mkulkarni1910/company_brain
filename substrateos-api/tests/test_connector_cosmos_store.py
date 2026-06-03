"""CosmosConnectionStore round-trips via a tiny in-memory fake of the Gremlin
client that understands the store's own query shapes (upsert / values / drop)."""
import re

import pytest

from app.connectors.cosmos_store import CosmosConnectionStore
from app.connectors.models import ActivityEntry, Connection, SyncJob


class FakeGraph:
    def __init__(self):
        self.store = {}  # (label, tenant, key) -> data

    async def submit(self, query, bindings=None):
        b = bindings or {}
        label = re.search(r"has\('([^']+)'", query).group(1)
        tid = b.get("tid")
        if "addV" in query and "property('data'" in query:        # upsert
            self.store[(label, tid, b["k"])] = b["d"]
            return []
        if ".drop()" in query:                                     # delete
            self.store.pop((label, tid, b["k"]), None)
            return []
        if ".values('data')" in query:                            # read
            if "k" in b:
                v = self.store.get((label, tid, b["k"]))
                return [v] if v is not None else []
            return [v for (lbl, t, _), v in self.store.items() if lbl == label and t == tid]
        return []


def _conn(cid="c1", tenant="t", **kw):
    return Connection(connection_id=cid, tenant_id=tenant, site_id="s", name="Sales",
                      web_url="https://x", **kw)


@pytest.mark.asyncio
async def test_connection_crud():
    st = CosmosConnectionStore(graph=FakeGraph())
    await st.put_connection(_conn(status="syncing"))
    assert [c.name for c in await st.list_connections("t")] == ["Sales"]
    c = await st.get_connection("t", "c1")
    assert c.status == "syncing"
    c.status = "live"; c.item_count = 7
    await st.put_connection(c)
    assert (await st.get_connection("t", "c1")).item_count == 7
    await st.delete_connection("t", "c1")
    assert await st.list_connections("t") == []
    assert await st.get_connection("t", "missing") is None


@pytest.mark.asyncio
async def test_job_roundtrip():
    st = CosmosConnectionStore(graph=FakeGraph())
    await st.put_job(SyncJob(job_id="j1", tenant_id="t", connection_id="c1", status="running", total=4))
    assert (await st.get_job("t", "j1")).total == 4
    assert await st.get_job("t", "nope") is None


@pytest.mark.asyncio
async def test_activity_rolls_and_caps():
    import datetime
    st = CosmosConnectionStore(graph=FakeGraph())
    for i in range(3):
        await st.log_activity("t", ActivityEntry(ts=datetime.datetime(2026, 1, 1),
                              actor="admin", text=f"e{i}", kind="sync"))
    items = await st.recent_activity("t")
    assert [a.text for a in items] == ["e2", "e1", "e0"]


@pytest.mark.asyncio
async def test_reads_degrade_on_error():
    class Boom:
        async def submit(self, q, b=None): raise RuntimeError("cosmos down")
    st = CosmosConnectionStore(graph=Boom())
    assert await st.list_connections("t") == []
    assert await st.get_connection("t", "c1") is None
    assert await st.get_job("t", "j1") is None
    assert await st.recent_activity("t") == []
    await st.put_connection(_conn())   # must not raise
