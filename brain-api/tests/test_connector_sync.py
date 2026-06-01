import pytest
from app.connectors.models import Connection, RemoteFile, SyncJob
from app.connectors.store import ConnectionStore
from app.connectors.sync import SyncRunner
from tests.test_connector_store import FakeRedis  # reuse

class FakeConnector:
    def __init__(self, files, contents): self._f=files; self._c=contents
    async def list_files(self, site_id, max_items=None): return self._f
    async def fetch_content(self, drive_id, item_id): return self._c.get(item_id)

class FakePipeline:
    def __init__(self): self.calls=[]
    async def process(self, doc):
        self.calls.append(doc)
        class R: chunks_indexed=2; doc_id=doc.doc_id
        return R()

@pytest.mark.asyncio
async def test_sync_ingests_supported_files_and_completes():
    files=[RemoteFile(drive_id="d",item_id="i1",name="a.md",web_url="https://x/a",size=5),
           RemoteFile(drive_id="d",item_id="i2",name="b.md",web_url="https://x/b",size=5)]
    conn=FakeConnector(files, {"i1": b"# A", "i2": b"# B"})
    pipe=FakePipeline(); store=ConnectionStore(client=FakeRedis())
    c=Connection(connection_id="c1",tenant_id="t",site_id="s",name="Sales",web_url="https://x")
    await store.put_connection(c)
    runner=SyncRunner(connector=conn, pipeline=pipe, store=store)
    job=await runner.run(connection=c, actor="admin")
    assert job.status=="succeeded" and job.processed==2 and job.skipped==0
    assert {d.source for d in pipe.calls}=={"sharepoint"}
    assert pipe.calls[0].doc_id=="sp:s:i1" and pipe.calls[0].acl_principals==["t:everyone"]
    refreshed=await store.get_connection("t","c1")
    assert refreshed.status=="live" and refreshed.item_count==2

@pytest.mark.asyncio
async def test_sync_skips_unfetchable_and_counts():
    files=[RemoteFile(drive_id="d",item_id="i1",name="a.md",web_url="https://x",size=5),
           RemoteFile(drive_id="d",item_id="i2",name="b.md",web_url="https://x",size=5)]
    conn=FakeConnector(files, {"i1": b"# A"})  # i2 content missing -> skip
    pipe=FakePipeline(); store=ConnectionStore(client=FakeRedis())
    c=Connection(connection_id="c1",tenant_id="t",site_id="s",name="S",web_url="https://x")
    runner=SyncRunner(connector=conn, pipeline=pipe, store=store)
    job=await runner.run(connection=c, actor="admin")
    assert job.processed==1 and job.skipped==1 and job.status=="succeeded"

@pytest.mark.asyncio
async def test_sync_no_files_marks_live_zero():
    conn=FakeConnector([], {}); pipe=FakePipeline(); store=ConnectionStore(client=FakeRedis())
    c=Connection(connection_id="c1",tenant_id="t",site_id="s",name="S",web_url="https://x")
    runner=SyncRunner(connector=conn, pipeline=pipe, store=store)
    job=await runner.run(connection=c, actor="admin")
    assert job.status=="succeeded" and job.total==0
