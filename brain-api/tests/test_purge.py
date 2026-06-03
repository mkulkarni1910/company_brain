import asyncio
import fnmatch

import app.retrieval.ai_search_client as aisc
from app.acl.store import ACLStore
from app.activity.store import ActivityStore


class _FakeSearchCli:
    """Async SearchClient stand-in: pages keyed by skip//top; records deletes."""

    def __init__(self, pages: list[list[str]]) -> None:
        self._pages = pages
        self.deleted: list[str] = []
        self.last_filter: str | None = None

    async def search(self, *, search_text, filter, select, top, skip):
        self.last_filter = filter
        idx = skip // top
        rows = self._pages[idx] if idx < len(self._pages) else []

        async def _gen():
            for cid in rows:
                yield {"chunk_id": cid}

        return _gen()

    async def delete_documents(self, *, documents):
        self.deleted.extend(d["chunk_id"] for d in documents)


def _client(cli) -> aisc.AISearchClient:
    c = aisc.AISearchClient.__new__(aisc.AISearchClient)
    c._credential = None
    c._cli = cli
    return c


def test_delete_tenant_docs_pages_and_deletes(monkeypatch):
    monkeypatch.setattr(aisc, "_DELETE_PAGE", 2)
    cli = _FakeSearchCli([["a", "b"], ["c"]])  # full page (2) then partial (1) -> stop
    n = asyncio.run(_client(cli).delete_tenant_docs(tenant_id="t-eval"))
    assert n == 3
    assert sorted(cli.deleted) == ["a", "b", "c"]


def test_delete_tenant_docs_empty(monkeypatch):
    monkeypatch.setattr(aisc, "_DELETE_PAGE", 2)
    cli = _FakeSearchCli([[]])
    n = asyncio.run(_client(cli).delete_tenant_docs(tenant_id="t-eval"))
    assert n == 0
    assert cli.deleted == []


def test_delete_tenant_docs_escapes_single_quote(monkeypatch):
    monkeypatch.setattr(aisc, "_DELETE_PAGE", 2)
    cli = _FakeSearchCli([[]])
    asyncio.run(_client(cli).delete_tenant_docs(tenant_id="o'brien"))
    assert cli.last_filter == "tenant_id eq 'o''brien'"


# ---------------------------------------------------------------------------
# ACLStore.clear_tenant
# ---------------------------------------------------------------------------


class _FakeRedisAcl:
    def __init__(self, keys) -> None:
        self._keys = set(keys)
        self.deleted: list[str] = []

    async def scan_iter(self, match, count=500):
        for k in list(self._keys):
            if fnmatch.fnmatch(k, match):
                yield k

    async def delete(self, k):
        self.deleted.append(k)
        self._keys.discard(k)


def test_clear_tenant_noop_without_redis():
    store = ACLStore.__new__(ACLStore)
    store._r = None
    assert asyncio.run(store.clear_tenant(tenant_id="t-eval")) is None


def test_clear_tenant_deletes_only_matching():
    store = ACLStore.__new__(ACLStore)
    store._r = _FakeRedisAcl(
        {"acl:doc:t-eval:d1", "acl:doc:t-eval:d2", "acl:doc:other:x"}
    )
    n = asyncio.run(store.clear_tenant(tenant_id="t-eval"))
    assert n == 2
    assert sorted(store._r.deleted) == ["acl:doc:t-eval:d1", "acl:doc:t-eval:d2"]


# ---------------------------------------------------------------------------
# ActivityStore.purge_tenant
# ---------------------------------------------------------------------------


def test_purge_tenant_noop_without_cluster():
    store = ActivityStore.__new__(ActivityStore)
    store._client = None
    store._db = "brain"
    assert asyncio.run(store.purge_tenant(tenant_id="t-eval")) is None
