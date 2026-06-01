from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import get_conversation_store
from app.domain.conversation import Conversation, ConversationSummary, ConversationTurn
from app.domain.query import Answer
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeStore:
    async def list(self, *, user, limit=100):
        return [ConversationSummary(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC), turn_count=1)]
    async def get(self, *, user, conversation_id):
        if conversation_id != "c1":
            return None
        return Conversation(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC),
            turns=[ConversationTurn(query="pto?", answer=Answer(text="20 days", citations=[], query_id=""),
                                    ts=datetime(2026, 6, 1, tzinfo=UTC))])


def test_conversations_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/conversations").status_code == 401


def test_list_conversations() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: FakeStore()
    try:
        with TestClient(app) as client:
            r = client.get("/conversations", headers=_HDR)
        assert r.status_code == 200 and r.json()[0]["id"] == "c1"
    finally:
        app.dependency_overrides.clear()


def test_get_conversation() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: FakeStore()
    try:
        with TestClient(app) as client:
            r = client.get("/conversations/c1", headers=_HDR)
        assert r.status_code == 200
        assert r.json()["turns"][0]["answer"]["text"] == "20 days"
    finally:
        app.dependency_overrides.clear()


def test_get_missing_conversation_404() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: FakeStore()
    try:
        with TestClient(app) as client:
            r = client.get("/conversations/nope", headers=_HDR)
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_list_empty_when_store_unavailable() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: None
    try:
        with TestClient(app) as client:
            r = client.get("/conversations", headers=_HDR)
        assert r.status_code == 200 and r.json() == []
    finally:
        app.dependency_overrides.clear()
