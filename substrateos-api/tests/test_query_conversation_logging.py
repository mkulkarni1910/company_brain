from fastapi.testclient import TestClient

from app.deps import get_conversation_store, get_orchestrator
from app.domain.query import Answer
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class _Orch:
    async def answer(self, body, *, user, user_token=None, skill_context=None):
        return Answer(text="x", citations=[], query_id="q1")


class _Store:
    def __init__(self): self.appended = []
    async def append(self, *, user, conversation_id, query, answer):
        self.appended.append((conversation_id, query))


def _run(payload):
    store = _Store()
    app.dependency_overrides[get_orchestrator] = lambda: _Orch()
    app.dependency_overrides[get_conversation_store] = lambda: store
    try:
        with TestClient(app) as client:
            resp = client.post("/query", json=payload, headers=_HDR)
        return resp, store
    finally:
        app.dependency_overrides.clear()


def test_logs_when_conversation_id_present() -> None:
    resp, store = _run({"query": "pto?", "conversation_id": "c1"})
    assert resp.status_code == 200
    assert store.appended == [("c1", "pto?")]


def test_no_log_without_conversation_id() -> None:
    resp, store = _run({"query": "pto?"})
    assert resp.status_code == 200
    assert store.appended == []
