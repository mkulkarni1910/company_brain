import pytest

from app.people.graph_client import PeopleGraphClient


class FakeGraph(PeopleGraphClient):
    def __init__(self, rows, *, fail=False):
        self._rows = rows
        self._fail = fail
        self.last = None
    async def submit(self, query, bindings=None):
        self.last = (query, bindings)
        if self._fail:
            raise RuntimeError("gremlin down")
        return self._rows


@pytest.mark.asyncio
async def test_resolve_people_maps_names() -> None:
    g = FakeGraph([
        {"user_id": ["u1"], "display_name": ["Priya Nair"]},
        {"user_id": ["u2"], "display_name": ["Sam Osei"]},
    ])
    out = await g.resolve_people(["u1", "u2"], tenant_id="t1")
    names = {p.user_id: p.display_name for p in out}
    assert names == {"u1": "Priya Nair", "u2": "Sam Osei"}


@pytest.mark.asyncio
async def test_resolve_people_empty_and_degrades() -> None:
    assert await FakeGraph([]).resolve_people([], tenant_id="t1") == []
    assert await FakeGraph([], fail=True).resolve_people(["u1"], tenant_id="t1") == []
