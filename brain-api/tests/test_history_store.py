import pytest

from app.domain.identity import User
from app.history.store import HistoryStore


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.fail = False

    async def lpush(self, key, value):
        if self.fail:
            raise ConnectionError("down")
        self.store.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, end):
        if self.fail:
            raise ConnectionError("down")
        self.store[key] = self.store.get(key, [])[start : end + 1]

    async def lrange(self, key, start, end):
        if self.fail:
            raise ConnectionError("down")
        return self.store.get(key, [])[start : end + 1]


def _user() -> User:
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids=set())


@pytest.mark.asyncio
async def test_add_then_recent_is_newest_first() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    await s.add(user=_user(), query="first", query_id="q1")
    await s.add(user=_user(), query="second", query_id="q2")
    out = await s.recent(user=_user())
    assert [e.query for e in out] == ["second", "first"]


@pytest.mark.asyncio
async def test_caps_at_50() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    for i in range(60):
        await s.add(user=_user(), query=f"q{i}", query_id=str(i))
    assert len(r.store["history:t1:u1"]) == 50


@pytest.mark.asyncio
async def test_recent_degrades_to_empty_on_error() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    await s.add(user=_user(), query="x", query_id="q")
    r.fail = True
    assert await s.recent(user=_user()) == []
