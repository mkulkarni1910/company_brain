from datetime import UTC, datetime

import pytest

from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient


def _row(doc_id, chunk_id, *, source="sharepoint", highlights=None):
    now = datetime(2026, 5, 31, tzinfo=UTC).isoformat()
    r = {"chunk_id": chunk_id, "doc_id": doc_id, "tenant_id": "t1", "source": source,
         "source_url": f"http://x/{doc_id}", "title": doc_id.upper(), "content": "full body text here",
         "author_id": "u1", "acl_principals": ["t1:everyone"], "entities": [],
         "created_at": now, "modified_at": now, "chunk_index": 0}
    if highlights is not None:
        r["@search.highlights"] = highlights
    return r


class FakeResults:
    def __init__(self, rows, *, facets, count):
        self._rows = rows
        self._facets = facets
        self._count = count
    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield dict(r)
        return gen()
    async def get_facets(self):
        return self._facets
    async def get_count(self):
        return self._count


class FakeCli:
    def __init__(self, results):
        self._results = results
        self.kwargs = None
    async def search(self, **kwargs):
        self.kwargs = kwargs
        return self._results


def _client(results) -> AISearchClient:
    c = AISearchClient.__new__(AISearchClient)
    c._cli = FakeCli(results)
    return c


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids={"t1:everyone"})


@pytest.mark.asyncio
async def test_search_page_dedupes_facets_total_and_filters() -> None:
    rows = [
        _row("d1", "d1#0", highlights={"content": ["a <b>vision</b> b"]}),
        _row("d1", "d1#1"),
        _row("d2", "d2#0", source="teams"),
    ]
    results = FakeResults(rows, facets={"source": [{"value": "sharepoint", "count": 5},
                                                   {"value": "teams", "count": 2}]}, count=7)
    c = _client(results)
    page = await c.search_page(query="vision", user=_user(), vector=[0.1, 0.2], top=10,
                               sources=["sharepoint", "teams"], author_id="u1")
    assert [h.doc_id for h in page.results] == ["d1", "d2"]
    assert page.results[0].snippet == "a vision b"
    assert page.total == 7
    assert {f.source: f.count for f in page.facets} == {"sharepoint": 5, "teams": 2}
    flt = c._cli.kwargs["filter"]
    assert "tenant_id eq 't1'" in flt
    assert "search.in(source, 'sharepoint,teams', ',')" in flt
    assert "author_id eq 'u1'" in flt


@pytest.mark.asyncio
async def test_search_page_degrades_to_empty_on_error() -> None:
    class Boom:
        async def search(self, **k):
            raise RuntimeError("search down")
    c = AISearchClient.__new__(AISearchClient)
    c._cli = Boom()
    page = await c.search_page(query="x", user=_user(), vector=[0.1])
    assert page.results == [] and page.facets == [] and page.total == 0


@pytest.mark.asyncio
async def test_date_from_naive_is_coerced_to_utc() -> None:
    from datetime import datetime
    results = FakeResults([], facets={"source": []}, count=0)
    c = _client(results)
    await c.search_page(query="x", user=_user(), vector=[0.1],
                        date_from=datetime(2026, 1, 15, 10, 30))  # naive
    flt = c._cli.kwargs["filter"]
    assert "modified_at ge 2026-01-15T10:30:00+00:00" in flt


@pytest.mark.asyncio
async def test_author_id_single_quote_escaped() -> None:
    results = FakeResults([], facets={"source": []}, count=0)
    c = _client(results)
    await c.search_page(query="x", user=_user(), vector=[0.1], author_id="o'brien")
    assert "author_id eq 'o''brien'" in c._cli.kwargs["filter"]


@pytest.mark.asyncio
async def test_top_limits_result_count() -> None:
    rows = [_row("d1", "d1#0"), _row("d2", "d2#0"), _row("d3", "d3#0")]
    results = FakeResults(rows, facets={"source": []}, count=3)
    page = await _client(results).search_page(query="x", user=_user(), vector=[0.1], top=1)
    assert len(page.results) == 1 and page.results[0].doc_id == "d1"
