import json

import pytest

from app.conversations.store import ConversationStore
from app.domain.identity import User
from app.domain.query import Answer, Citation


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def all(self):
        class _F:
            def __init__(s, r): s._r = r
            def result(s): return s._r
        return _F(self._rows)


class FakeGremlin:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
    def submit(self, query, bindings=None):
        self.calls.append((query, bindings or {}))
        return FakeResult(self._responses.pop(0) if self._responses else [])
    def close(self): pass


def _store(responses):
    return ConversationStore(gremlin_client=FakeGremlin(responses))


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids=set())


def _answer():
    return Answer(text="20 days", citations=[Citation(
        doc_id="d1", chunk_id="d1#0", source_url="http://x", title="PTO", snippet="..")], query_id="q1")


@pytest.mark.asyncio
async def test_append_creates_and_caps() -> None:
    g = FakeGremlin([[], []])
    store = ConversationStore(gremlin_client=g)
    await store.append(user=_user(), conversation_id="c1", query="how much pto?", answer=_answer())
    upsert_query, b = g.calls[1]
    assert "coalesce(unfold()" in upsert_query and "addV('conversation')" in upsert_query
    assert "has('user_id', uid)" in upsert_query
    turns = json.loads(b["tj"])
    assert turns[-1]["q"] == "how much pto?"
    assert turns[-1]["a"]["text"] == "20 days"
    assert turns[-1]["a"]["citations"][0]["doc_id"] == "d1"
    assert b["tc"] == 1 and b["title"] == "how much pto?"


@pytest.mark.asyncio
async def test_append_caps_at_50() -> None:
    existing = [{"q": f"q{i}", "a": {"text": "x", "citations": []}, "ts": "t"} for i in range(55)]
    g = FakeGremlin([[{"turns_json": [json.dumps(existing)]}], []])
    store = ConversationStore(gremlin_client=g)
    await store.append(user=_user(), conversation_id="c1", query="newest", answer=_answer())
    _, b = g.calls[1]
    turns = json.loads(b["tj"])
    assert len(turns) == 50
    assert turns[-1]["q"] == "newest"
    assert b["tc"] == 50


@pytest.mark.asyncio
async def test_append_degrades_on_error() -> None:
    class Boom:
        def submit(self, *a, **k): raise RuntimeError("cosmos down")
        def close(self): pass
    store = ConversationStore(gremlin_client=Boom())
    await store.append(user=_user(), conversation_id="c1", query="x", answer=_answer())  # must not raise


@pytest.mark.asyncio
async def test_list_parses_and_orders() -> None:
    rows = [{"id": "c2", "title": "newer", "updated_at": "2026-06-01T10:00:00+00:00", "turn_count": 2},
            {"id": "c1", "title": "older", "updated_at": "2026-05-31T10:00:00+00:00", "turn_count": 1}]
    out = await _store([rows]).list(user=_user())
    assert [s.id for s in out] == ["c2", "c1"]
    assert out[0].turn_count == 2


@pytest.mark.asyncio
async def test_get_parses_turns() -> None:
    tj = json.dumps([{"q": "pto?", "a": {"text": "20 days", "citations": [
        {"doc_id": "d1", "chunk_id": "d1#0", "source_url": "http://x", "title": "PTO", "snippet": ".."}]},
        "ts": "2026-06-01T10:00:00+00:00"}])
    vm = [{"conv_id": ["c1"], "title": ["pto?"], "created_at": ["2026-06-01T09:00:00+00:00"],
           "updated_at": ["2026-06-01T10:00:00+00:00"], "turns_json": [tj]}]
    conv = await _store([vm]).get(user=_user(), conversation_id="c1")
    assert conv is not None
    assert conv.id == "c1" and conv.title == "pto?"
    assert conv.turns[0].answer.text == "20 days"
    assert conv.turns[0].answer.citations[0].doc_id == "d1"


@pytest.mark.asyncio
async def test_get_missing_returns_none() -> None:
    assert await _store([[]]).get(user=_user(), conversation_id="nope") is None
