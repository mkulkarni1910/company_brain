import pytest
from app.connectors.models import Connection, SyncJob, ActivityEntry
from app.connectors.store import ConnectionStore

class FakeRedis:
    def __init__(self): self.h={}; self.kv={}; self.lists={}
    async def hset(self, k, field, val): self.h.setdefault(k,{})[field]=val
    async def hgetall(self, k): return dict(self.h.get(k,{}))
    async def hdel(self, k, field): self.h.get(k,{}).pop(field,None)
    async def set(self, name, value, ex=None): self.kv[name]=value
    async def get(self, name): return self.kv.get(name)
    async def lpush(self, k, v): self.lists.setdefault(k,[]).insert(0,v)
    async def ltrim(self, k, a, b): self.lists[k]=self.lists.get(k,[])[a:b+1]
    async def lrange(self, k, a, b):
        xs=self.lists.get(k,[]); return xs[a:(b+1) if b>=0 else None]
    def pipeline(self, transaction=False):
        store=self
        class P:
            def __init__(s): s.ops=[]
            def lpush(s,k,v): s.ops.append(("lpush",k,v)); return s
            def ltrim(s,k,a,b): s.ops.append(("ltrim",k,a,b)); return s
            async def execute(s):
                for op in s.ops:
                    await getattr(store, op[0])(*op[1:])
            async def __aenter__(s): return s
            async def __aexit__(s,*a): return False
        return P()

@pytest.mark.asyncio
async def test_connection_crud():
    st = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", type="sharepoint",
                   site_id="s", name="Sales", web_url="https://x")
    await st.put_connection(c)
    got = await st.list_connections("t")
    assert len(got)==1 and got[0].name=="Sales"
    c.status="live"; c.item_count=12; await st.put_connection(c)
    assert (await st.get_connection("t","c1")).item_count==12
    await st.delete_connection("t","c1")
    assert await st.list_connections("t")==[]

@pytest.mark.asyncio
async def test_job_roundtrip():
    st = ConnectionStore(client=FakeRedis())
    j = SyncJob(job_id="j1", tenant_id="t", connection_id="c1", status="running", total=3)
    await st.put_job(j)
    assert (await st.get_job("t","j1")).total==3
    assert await st.get_job("t","missing") is None

@pytest.mark.asyncio
async def test_activity_log_caps():
    import datetime
    st = ConnectionStore(client=FakeRedis())
    for i in range(3):
        await st.log_activity("t", ActivityEntry(ts=datetime.datetime(2026,1,1),
            actor="admin", text=f"e{i}", kind="connect"))
    items = await st.recent_activity("t")
    assert items[0].text=="e2" and len(items)==3

@pytest.mark.asyncio
async def test_degrades_on_error():
    class Boom:
        async def hgetall(self,k): raise ConnectionError("down")
        async def get(self,n): raise ConnectionError("down")
        async def lrange(self,k,a,b): raise ConnectionError("down")
    st = ConnectionStore(client=Boom())
    assert await st.list_connections("t")==[]
    assert await st.get_job("t","j")==None
    assert await st.recent_activity("t")==[]
