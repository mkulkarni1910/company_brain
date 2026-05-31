import pytest

from app.activity.store import ActivityStore


class _Resp:
    def __init__(self, rows):
        self.primary_results = [rows]


class FakeKusto:
    def __init__(self, rows, *, fail=False):
        self._rows = rows
        self.fail = fail
        self.last_query = None

    def execute_query(self, db, query, crp):
        self.last_query = query
        if self.fail:
            raise RuntimeError("adx down")
        return _Resp(self._rows)


def _store(fake) -> ActivityStore:
    s = ActivityStore.__new__(ActivityStore)  # bypass __init__ (no real cluster)
    s._db = "brain"
    s._client = fake
    return s


@pytest.mark.asyncio
async def test_trending_parses_and_orders() -> None:
    fake = FakeKusto([{"DocId": "d1", "score": 5.0}, {"DocId": "d2", "score": 2.0}])
    out = await _store(fake).trending(tenant_id="t1", window_days=14, limit=8)
    assert out == [("d1", 5.0), ("d2", 2.0)]
    assert "top 8 by score desc" in fake.last_query
    assert "ago(14d)" in fake.last_query


@pytest.mark.asyncio
async def test_trending_degrades_to_empty() -> None:
    out = await _store(FakeKusto([], fail=True)).trending(tenant_id="t1")
    assert out == []


@pytest.mark.asyncio
async def test_source_breakdown_parses() -> None:
    fake = FakeKusto([{"Source": "sharepoint", "events": 4, "score": 6.0}])
    out = await _store(fake).source_breakdown(
        tenant_id="t1", doc_ids=["d1", "d2"], window_days=14
    )
    assert out == [("sharepoint", 4, 6.0)]


@pytest.mark.asyncio
async def test_source_breakdown_empty_doc_ids() -> None:
    out = await _store(FakeKusto([])).source_breakdown(tenant_id="t1", doc_ids=[])
    assert out == []
