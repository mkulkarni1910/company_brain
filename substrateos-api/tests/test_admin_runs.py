"""Tests for admin conversation-runs: tenant-scoped store reads + endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.admin_runs import _surface
from app.conversations.store import ConversationStore
from app.deps import get_conversation_store
from app.domain.conversation import Conversation, ConversationTurn
from app.domain.query import Answer
from app.main import app


# ── FakeGremlin (mirrors test_conversation_store) ─────────────────────────────

class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self):
        class _F:
            def __init__(s, r): s._r = r
            def result(s): return s._r
        return _F(self._rows)


class _Gremlin:
    def __init__(self, responses): self._responses = list(responses); self.calls = []
    def submit(self, query, bindings=None):
        self.calls.append((query, bindings or {}))
        return _Result(self._responses.pop(0) if self._responses else [])
    def close(self): pass


# ── surface derivation ────────────────────────────────────────────────────────

def test_surface_from_conversation_id():
    assert _surface("slack:C1:1.0") == "slack"
    assert _surface("teams:19:abc") == "teams"
    assert _surface("a1b2c3-uuid") == "web"


# ── store: list_all / get_any (no user filter, tenant-scoped) ─────────────────

@pytest.mark.asyncio
async def test_list_all_is_tenant_scoped_and_returns_user_id():
    rows = [{"id": "web-1", "title": "PTO?", "updated_at": "2026-06-06T10:41:00+00:00",
             "turn_count": 1, "user_id": "u1"}]
    g = _Gremlin([rows])
    store = ConversationStore(gremlin_client=g)
    out = await store.list_all(tenant_id="t1", limit=50)
    # query filters by tenant only — NOT user_id
    q, _b = g.calls[0]
    assert "has('user_id'" not in q
    assert out[0]["id"] == "web-1" and out[0]["user_id"] == "u1" and out[0]["turn_count"] == 1


@pytest.mark.asyncio
async def test_get_any_returns_conversation_and_user_id():
    turns = [{"q": "what is pto?", "a": {"text": "20 days", "citations": []},
              "ts": "2026-06-06T10:41:02+00:00"}]
    vm = [{"conv_id": ["web-1"], "title": ["PTO?"], "created_at": ["2026-06-06T10:41:00+00:00"],
           "updated_at": ["2026-06-06T10:41:03+00:00"], "turns_json": [json.dumps(turns)],
           "user_id": ["u1"]}]
    store = ConversationStore(gremlin_client=_Gremlin([vm]))
    res = await store.get_any(tenant_id="t1", conversation_id="web-1")
    assert res is not None
    assert res["user_id"] == "u1"
    assert res["conversation"].turns[0].query == "what is pto?"
    assert res["conversation"].turns[0].answer.text == "20 days"


@pytest.mark.asyncio
async def test_get_any_none_when_missing():
    store = ConversationStore(gremlin_client=_Gremlin([[]]))
    assert await store.get_any(tenant_id="t1", conversation_id="nope") is None


# ── endpoints (admin-key gated) ───────────────────────────────────────────────

class _FakeConvStore:
    async def list_all(self, *, tenant_id, limit=50):
        return [
            {"id": "slack:C1:1.0", "title": "Refund?", "updated_at": "2026-06-06T10:00:00+00:00", "turn_count": 2, "user_id": "bot"},
            {"id": "web-uuid", "title": "PTO?", "updated_at": "2026-06-06T10:41:00+00:00", "turn_count": 1, "user_id": "u1"},
        ]

    async def get_any(self, *, tenant_id, conversation_id):
        if conversation_id != "web-uuid":
            return None
        conv = Conversation(
            id="web-uuid", title="PTO?", updated_at=datetime(2026, 6, 6, 10, 41, 3, tzinfo=UTC),
            turns=[ConversationTurn(query="what is pto?",
                                    answer=Answer(text="20 days", citations=[], query_id=""),
                                    ts=datetime(2026, 6, 6, 10, 41, 2, tzinfo=UTC))],
        )
        return {"conversation": conv, "user_id": "bot"}  # bot → asker stays None


def test_conversation_runs_endpoints(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_conversation_store] = lambda: _FakeConvStore()
    try:
        with TestClient(app) as client:
            # gating: no key → 403
            assert client.get("/admin/conversation-runs").status_code == 403

            r = client.get("/admin/conversation-runs", headers={"x-admin-key": "k"})
            assert r.status_code == 200
            body = r.json()
            assert {b["surface"] for b in body} == {"slack", "web"}

            d = client.get("/admin/conversation-runs/web-uuid", headers={"x-admin-key": "k"})
            assert d.status_code == 200
            detail = d.json()
            assert detail["surface"] == "web"
            assert detail["asker"] is None  # stored under bot → no name
            assert detail["turns"][0]["query"] == "what is pto?"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
