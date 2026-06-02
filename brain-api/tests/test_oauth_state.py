"""OAuth CSRF state store — one-shot + TTL + provider — on Cosmos (fake graph) and Redis (fake)."""
import re

import pytest

from app.connectors.cosmos_store import CosmosConnectionStore
from app.connectors.store import ConnectionStore


class FakeGraph:
    """Understands the store's upsert / values-by-key / drop-by-key shapes.
    OAuth state is looked up by `st` only (tenant-agnostic), so key lookups here
    ignore the partition, matching the real cross-partition Gremlin query."""

    def __init__(self):
        self.store = {}  # (label, key) -> data

    async def submit(self, query, bindings=None):
        b = bindings or {}
        label = re.search(r"has\('([^']+)'", query).group(1)
        if "addV" in query and "property('data'" in query:
            self.store[(label, b["k"])] = b["d"]
            return []
        if ".drop()" in query:
            self.store.pop((label, b["k"]), None)
            return []
        if ".values('data')" in query and "k" in b:
            v = self.store.get((label, b["k"]))
            return [v] if v is not None else []
        return []


@pytest.mark.asyncio
async def test_cosmos_state_provider_roundtrip():
    st = CosmosConnectionStore(graph=FakeGraph())
    await st.put_oauth_state("s1", "t-eval", "teams")
    result = await st.consume_oauth_state("s1")
    assert result == ("t-eval", "teams")
    assert await st.consume_oauth_state("s1") is None   # one-shot
    assert await st.consume_oauth_state("missing") is None


@pytest.mark.asyncio
async def test_cosmos_state_one_shot():
    st = CosmosConnectionStore(graph=FakeGraph())
    await st.put_oauth_state("s1", "t-eval", "sharepoint")
    assert await st.consume_oauth_state("s1") == ("t-eval", "sharepoint")
    assert await st.consume_oauth_state("s1") is None     # one-shot
    assert await st.consume_oauth_state("missing") is None


@pytest.mark.asyncio
async def test_cosmos_state_expired(monkeypatch):
    import app.connectors.cosmos_store as cs
    st = CosmosConnectionStore(graph=FakeGraph())
    await st.put_oauth_state("s2", "t-eval", "sharepoint")
    # jump time past the TTL
    real = cs.time.time()
    monkeypatch.setattr(cs.time, "time", lambda: real + 10_000)
    assert await st.consume_oauth_state("s2") is None


class FakeRedis:
    def __init__(self): self.kv = {}
    async def set(self, name, value, ex=None): self.kv[name] = value
    async def getdel(self, name): return self.kv.pop(name, None)


@pytest.mark.asyncio
async def test_redis_state_provider_roundtrip():
    st = ConnectionStore(client=FakeRedis())
    await st.put_oauth_state("s1", "t-eval", "teams")
    result = await st.consume_oauth_state("s1")
    assert result == ("t-eval", "teams")
    assert await st.consume_oauth_state("s1") is None


@pytest.mark.asyncio
async def test_redis_state_one_shot():
    st = ConnectionStore(client=FakeRedis())
    await st.put_oauth_state("s1", "t-eval", "sharepoint")
    assert await st.consume_oauth_state("s1") == ("t-eval", "sharepoint")
    assert await st.consume_oauth_state("s1") is None
