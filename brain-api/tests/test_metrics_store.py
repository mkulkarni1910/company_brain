import pytest
from app.metrics.store import MetricsStore

class FakeRedis:
    def __init__(self): self.counts={}; self.hll={}
    async def incr(self, k): self.counts[k]=self.counts.get(k,0)+1; return self.counts[k]
    async def expire(self, k, ttl): pass
    async def mget(self, keys): return [self.counts.get(k) for k in keys]
    async def pfadd(self, k, *vals): self.hll.setdefault(k,set()).update(vals)
    async def pfcount(self, *keys):
        u=set()
        for k in keys: u|=self.hll.get(k,set())
        return len(u)

@pytest.mark.asyncio
async def test_query_counter_and_users():
    r=FakeRedis(); st=MetricsStore(client=r)
    for _ in range(3): await st.record_query("t","u-1")
    await st.record_query("t","u-2")
    assert await st.queries_last_7d("t")==4
    assert await st.active_users_7d("t")==2

@pytest.mark.asyncio
async def test_degrades_to_none():
    class Boom:
        async def incr(self,k): raise ConnectionError()
        async def pfadd(self,k,*v): raise ConnectionError()
        async def mget(self,ks): raise ConnectionError()
        async def pfcount(self,*ks): raise ConnectionError()
        async def expire(self,k,t): raise ConnectionError()
    st=MetricsStore(client=Boom())
    await st.record_query("t","u")  # must not raise
    assert await st.queries_last_7d("t") is None
    assert await st.active_users_7d("t") is None
