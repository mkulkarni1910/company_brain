from fastapi.testclient import TestClient

from app.deps import get_orchestrator
from app.domain.query import Answer
from app.main import app


class _FakeOrch:
    async def answer(self, body, *, user):  # noqa: ANN001, ANN201
        return Answer(text="x", citations=[], query_id="t")


def test_debug_header_ignored_when_flag_disabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "false")
    from app.config import get_settings

    get_settings.cache_clear()

    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?"},
            headers={"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"},
        )
    assert resp.status_code == 401


def test_debug_header_honored_when_flag_enabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrch()
    try:
        client = TestClient(app)
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?"},
            headers={"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"},
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "x"
    finally:
        app.dependency_overrides.clear()
