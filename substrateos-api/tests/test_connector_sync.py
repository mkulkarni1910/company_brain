"""SyncRunner tests — uses the unified collect_documents interface."""
from datetime import UTC, datetime

import pytest

from app.connectors.models import Connection
from app.connectors.store import ConnectionStore
from app.connectors.sync import CollectResult, SyncRunner
from app.domain.chunk import SourceDoc
from tests.test_connector_store import FakeRedis  # reuse


def _doc(n: int, tenant: str = "t") -> SourceDoc:
    now = datetime.now(UTC)
    return SourceDoc(
        doc_id=f"sp:s:i{n}", tenant_id=tenant, source="sharepoint",
        source_url=f"https://x/{n}", title=f"doc{n}", body=f"text {n}",
        author_id=None, acl_principals=[f"{tenant}:everyone"],
        created_at=now, modified_at=now, mime="text/plain",
    )


class FakeConnector:
    def __init__(self, result: CollectResult):
        self._result = result

    async def collect_documents(self, cap: int) -> CollectResult:
        return self._result


class FakePipeline:
    def __init__(self):
        self.calls = []

    async def process(self, doc):
        self.calls.append(doc)
        class R:
            chunks_indexed = 2
            doc_id = doc.doc_id
        return R()


@pytest.mark.asyncio
async def test_sync_ingests_docs_and_completes():
    docs = [_doc(1), _doc(2)]
    result = CollectResult(docs=docs, skipped=0, truncated=False)
    conn_obj = FakeConnector(result)
    pipe = FakePipeline()
    store = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="Sales", web_url="https://x")
    await store.put_connection(c)
    runner = SyncRunner(connector=conn_obj, pipeline=pipe, store=store)
    job = await runner.run(connection=c, actor="admin")
    assert job.status == "succeeded"
    assert job.processed == 2
    assert job.skipped == 0
    assert job.total == 2
    assert {d.source for d in pipe.calls} == {"sharepoint"}
    assert pipe.calls[0].doc_id == "sp:s:i1"
    refreshed = await store.get_connection("t", "c1")
    assert refreshed.status == "live" and refreshed.item_count == 2


@pytest.mark.asyncio
async def test_sync_propagates_skipped():
    docs = [_doc(1)]
    result = CollectResult(docs=docs, skipped=3, truncated=False)
    conn_obj = FakeConnector(result)
    pipe = FakePipeline()
    store = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="S", web_url="https://x")
    runner = SyncRunner(connector=conn_obj, pipeline=pipe, store=store)
    job = await runner.run(connection=c, actor="admin")
    assert job.processed == 1
    assert job.skipped == 3
    assert job.total == 4
    assert job.status == "succeeded"


@pytest.mark.asyncio
async def test_sync_no_docs_marks_live_zero():
    result = CollectResult(docs=[], skipped=0, truncated=False)
    conn_obj = FakeConnector(result)
    pipe = FakePipeline()
    store = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="S", web_url="https://x")
    runner = SyncRunner(connector=conn_obj, pipeline=pipe, store=store)
    job = await runner.run(connection=c, actor="admin")
    assert job.status == "succeeded" and job.total == 0
    refreshed = await store.get_connection("t", "c1")
    assert refreshed.status == "live" and refreshed.item_count == 0


@pytest.mark.asyncio
async def test_sync_counts_ingest_errors_and_continues():
    docs = [_doc(1), _doc(2)]
    result = CollectResult(docs=docs, skipped=0, truncated=False)
    conn_obj = FakeConnector(result)

    class BoomPipe:
        def __init__(self): self.n = 0
        async def process(self, doc):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("ingest boom")
            class R:
                chunks_indexed = 1
                doc_id = doc.doc_id
            return R()

    pipe = BoomPipe()
    store = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="S", web_url="https://x")
    runner = SyncRunner(connector=conn_obj, pipeline=pipe, store=store)
    job = await runner.run(connection=c, actor="admin")
    # one errored, one succeeded — sync still completes
    assert job.status == "succeeded"
    assert job.errors == 1
    assert job.processed == 1


@pytest.mark.asyncio
async def test_sync_truncated_propagates():
    docs = [_doc(1), _doc(2)]
    result = CollectResult(docs=docs, skipped=0, truncated=True)
    conn_obj = FakeConnector(result)

    class OkPipe:
        async def process(self, doc):
            class R:
                chunks_indexed = 1
                doc_id = doc.doc_id
            return R()

    store = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="S", web_url="https://x")
    runner = SyncRunner(connector=conn_obj, pipeline=OkPipe(), store=store)
    job = await runner.run(connection=c, actor="admin")
    assert job.total == 2 and job.truncated is True


@pytest.mark.asyncio
async def test_sync_connector_failure_marks_error():
    """If collect_documents raises, the job is marked failed and the connection is error."""
    class BoomConnector:
        async def collect_documents(self, cap):
            raise RuntimeError("network down")

    store = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="S", web_url="https://x")
    runner = SyncRunner(connector=BoomConnector(), pipeline=FakePipeline(), store=store)
    job = await runner.run(connection=c, actor="admin")
    assert job.status == "failed"
    refreshed = await store.get_connection("t", "c1")
    assert refreshed.status == "error"
