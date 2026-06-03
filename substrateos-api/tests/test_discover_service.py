from datetime import UTC, datetime

import pytest

from app.discover.service import DiscoverService
from app.domain.chunk import Chunk
from app.domain.identity import User


def _chunk(doc_id: str) -> Chunk:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    return Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t1", source="uploaded",
        source_url=f"http://x/{doc_id}", title=doc_id.upper(),
        content="some body content " * 20, acl_principals=["t1:everyone"],
        created_at=now, modified_at=now, chunk_index=0,
    )


class FakeActivity:
    def __init__(self, trending, sources):
        self._t = trending
        self._s = sources

    async def trending(self, *, tenant_id, window_days=14, limit=8):
        return self._t

    async def source_breakdown(self, *, tenant_id, doc_ids, window_days=14):
        return self._s


class FakeSearch:
    def __init__(self, docs):
        self._docs = docs

    async def lookup_docs(self, *, doc_ids, user):
        return {d: self._docs[d] for d in doc_ids if d in self._docs}


class FakeCache:
    def __init__(self):
        self.data = {}

    async def get_json(self, key):
        return self.data.get(key)

    async def set_json(self, key, value, ttl_seconds):
        self.data[key] = value


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U",
                group_ids={"t1:everyone"})


@pytest.mark.asyncio
async def test_orders_by_score_drops_inaccessible_and_caps() -> None:
    activity = FakeActivity(
        trending=[("d1", 5.0), ("d2", 9.0), ("dX", 7.0)],  # dX not accessible
        sources=[("uploaded", 12, 14.0)],
    )
    search = FakeSearch({"d1": _chunk("d1"), "d2": _chunk("d2")})
    cache = FakeCache()
    svc = DiscoverService(activity=activity, search=search, cache=cache)
    res = await svc.result(user=_user(), limit=8)
    assert [t.doc_id for t in res.trending] == ["d2", "d1"]  # score desc, dX dropped
    assert res.by_source[0].source == "uploaded"
    assert res.window_days == 14
    assert cache.data  # cached


@pytest.mark.asyncio
async def test_returns_cached_when_present() -> None:
    cache = FakeCache()
    cache.data["discover:t1:u1"] = {
        "trending": [], "by_source": [], "window_days": 14,
    }
    svc = DiscoverService(
        activity=FakeActivity([("d1", 1.0)], []),
        search=FakeSearch({"d1": _chunk("d1")}),
        cache=cache,
    )
    res = await svc.result(user=_user())
    assert res.trending == []  # served from cache, activity not consulted


@pytest.mark.asyncio
async def test_degrades_when_no_activity() -> None:
    svc = DiscoverService(
        activity=FakeActivity([], []), search=FakeSearch({}), cache=FakeCache()
    )
    res = await svc.result(user=_user())
    assert res.trending == [] and res.by_source == []
