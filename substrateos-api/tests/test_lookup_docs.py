# tests/test_lookup_docs.py
from datetime import UTC, datetime

import pytest

from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient


def _doc(doc_id: str, chunk_id: str) -> dict:
    now = datetime(2026, 5, 31, tzinfo=UTC).isoformat()
    return {
        "chunk_id": chunk_id, "doc_id": doc_id, "tenant_id": "t1", "source": "uploaded",
        "source_url": f"http://x/{doc_id}", "title": doc_id.upper(), "content": "body text",
        "acl_principals": ["t1:everyone"], "author_id": None, "entities": [],
        "created_at": now, "modified_at": now, "chunk_index": 0,
    }


class FakeSearchResults:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield dict(d)
        return gen()


class FakeSearchCli:
    def __init__(self, docs):
        self._docs = docs
        self.last_filter = None

    async def search(self, *, search_text, filter, top, select):  # noqa: A002
        self.last_filter = filter
        return FakeSearchResults(self._docs)


def _client(docs) -> AISearchClient:
    c = AISearchClient.__new__(AISearchClient)  # bypass __init__ (no real endpoint)
    c._cli = FakeSearchCli(docs)
    return c


@pytest.mark.asyncio
async def test_lookup_docs_dedupes_by_doc_and_builds_acl_filter() -> None:
    user = User(user_id="u1", tenant_id="t1", email="", display_name="U",
                group_ids={"t1:everyone"})
    docs = [_doc("d1", "d1#0"), _doc("d1", "d1#1"), _doc("d2", "d2#0")]
    c = _client(docs)
    out = await c.lookup_docs(doc_ids=["d1", "d2"], user=user)
    assert set(out.keys()) == {"d1", "d2"}
    assert out["d1"].chunk_id == "d1#0"  # first chunk wins
    assert "tenant_id eq 't1'" in c._cli.last_filter
    assert "search.in(doc_id, 'd1,d2', ',')" in c._cli.last_filter


@pytest.mark.asyncio
async def test_lookup_docs_empty_ids() -> None:
    user = User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids=set())
    assert await _client([]).lookup_docs(doc_ids=[], user=user) == {}
