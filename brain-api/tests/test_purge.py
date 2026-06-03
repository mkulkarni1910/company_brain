import asyncio

import app.retrieval.ai_search_client as aisc


class _FakeSearchCli:
    """Async SearchClient stand-in: pages keyed by skip//top; records deletes."""

    def __init__(self, pages: list[list[str]]) -> None:
        self._pages = pages
        self.deleted: list[str] = []

    async def search(self, *, search_text, filter, select, top, skip):
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
