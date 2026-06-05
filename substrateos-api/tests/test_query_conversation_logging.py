from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import get_conversation_store, get_orchestrator
from app.domain.conversation import Conversation, ConversationTurn
from app.domain.query import Answer
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class _Orch:
    def __init__(self): self.seen_history = None
    async def answer(self, body, *, user, user_token=None, skill_context=None, history=None):
        self.seen_history = history
        return Answer(text="x", citations=[], query_id="q1")


class _Store:
    def __init__(self, conv=None):
        self.appended = []
        self.conv = conv
    async def get(self, *, user, conversation_id):
        return self.conv
    async def append(self, *, user, conversation_id, query, answer):
        self.appended.append((conversation_id, query))
    async def aclose(self):
        pass


def _run(payload, conv=None):
    store = _Store(conv=conv)
    orch = _Orch()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_conversation_store] = lambda: store
    try:
        with TestClient(app) as client:
            # get_conversation_memory builds from app.state; point state at the fake
            app.state.conversation_store = store
            resp = client.post("/query", json=payload, headers=_HDR)
        return resp, store, orch
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "conversation_store"):
            del app.state.conversation_store


def test_logs_when_conversation_id_present() -> None:
    resp, store, _ = _run({"query": "pto?", "conversation_id": "c1"})
    assert resp.status_code == 200
    assert store.appended == [("c1", "pto?")]


def test_no_log_without_conversation_id() -> None:
    resp, store, _ = _run({"query": "pto?"})
    assert resp.status_code == 200
    assert store.appended == []


def test_history_loaded_and_passed_to_orchestrator() -> None:
    turn = ConversationTurn(
        query="my name is Tom",
        answer=Answer(text="Hi Tom", citations=[], query_id="h"),
        ts=datetime.now(UTC),
    )
    conv = Conversation(id="c1", title="t", updated_at=datetime.now(UTC), turns=[turn])
    resp, _, orch = _run({"query": "what was my name?", "conversation_id": "c1"}, conv=conv)
    assert resp.status_code == 200
    assert orch.seen_history is not None and orch.seen_history[0].query == "my name is Tom"


def test_no_history_without_conversation_id() -> None:
    resp, _, orch = _run({"query": "pto?"})
    assert resp.status_code == 200
    assert orch.seen_history == []
