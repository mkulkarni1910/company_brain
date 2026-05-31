from fastapi.testclient import TestClient

from app.deps import get_discover_service, get_history_store
from app.domain.discover import DiscoverResult, TrendingDoc
from app.domain.history import HistoryEntry
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeHistory:
    async def recent(self, *, user, limit=50):
        from datetime import UTC, datetime
        return [HistoryEntry(query="pto?", query_id="q1", ts=datetime.now(UTC))]


class FakeDiscover:
    async def result(self, *, user, window_days=14, limit=8):
        return DiscoverResult(
            trending=[TrendingDoc(doc_id="d1", title="T", source="uploaded",
                                  source_url="http://x", snippet="s", score=1.0)],
            by_source=[], window_days=14,
        )


def test_history_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/history").status_code == 401


def test_history_returns_entries() -> None:
    app.dependency_overrides[get_history_store] = lambda: FakeHistory()
    try:
        with TestClient(app) as client:
            resp = client.get("/history", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json()[0]["query"] == "pto?"
    finally:
        app.dependency_overrides.clear()


def test_history_empty_when_store_unavailable() -> None:
    app.dependency_overrides[get_history_store] = lambda: None
    try:
        with TestClient(app) as client:
            resp = client.get("/history", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.clear()


def test_discover_returns_result() -> None:
    app.dependency_overrides[get_discover_service] = lambda: FakeDiscover()
    try:
        with TestClient(app) as client:
            resp = client.get("/discover", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json()["trending"][0]["doc_id"] == "d1"
    finally:
        app.dependency_overrides.clear()


def test_discover_empty_when_service_unavailable() -> None:
    app.dependency_overrides[get_discover_service] = lambda: None
    try:
        with TestClient(app) as client:
            resp = client.get("/discover", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json() == {"trending": [], "by_source": [], "window_days": 14}
    finally:
        app.dependency_overrides.clear()
