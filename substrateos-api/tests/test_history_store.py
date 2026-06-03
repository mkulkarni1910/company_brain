import pytest

from app.domain.identity import User
from app.history.store import HistoryStore


class _FakePipe:
    def __init__(self, parent: "FakeRedis") -> None:
        self._p = parent
        self._ops: list[tuple] = []

    async def __aenter__(self) -> "_FakePipe":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def lpush(self, key: str, value: str) -> "_FakePipe":
        self._ops.append(("lpush", key, value))
        return self

    def ltrim(self, key: str, start: int, end: int) -> "_FakePipe":
        self._ops.append(("ltrim", key, start, end))
        return self

    async def execute(self) -> None:
        if self._p.fail:
            raise ConnectionError("down")
        for op in self._ops:
            if op[0] == "lpush":
                self._p.store.setdefault(op[1], []).insert(0, op[2])
            else:
                self._p.store[op[1]] = self._p.store.get(op[1], [])[op[2] : op[3] + 1]


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.fail = False

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)

    async def lpush(self, key: str, value: str) -> None:
        if self.fail:
            raise ConnectionError("down")
        self.store.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        if self.fail:
            raise ConnectionError("down")
        self.store[key] = self.store.get(key, [])[start : end + 1]

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
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
    out = await s.recent(user=_user(), limit=60)
    assert len(out) == 50


@pytest.mark.asyncio
async def test_recent_degrades_to_empty_on_error() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    await s.add(user=_user(), query="x", query_id="q")
    r.fail = True
    assert await s.recent(user=_user()) == []


@pytest.mark.asyncio
async def test_add_degrades_silently_on_error() -> None:
    r = FakeRedis()
    r.fail = True
    s = HistoryStore(client=r)
    await s.add(user=_user(), query="x", query_id="q")  # must not raise
    r.fail = False
    assert await s.recent(user=_user()) == []
