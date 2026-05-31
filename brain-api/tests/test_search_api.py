from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import get_search_service
from app.domain.query import Answer
from app.domain.search import SearchHit, SearchResponse, SourceFacet
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeSearchService:
    async def result(self, *, user, query, top=10, skip=0, sources=None, date_from=None, author_id=None):
        return SearchResponse(
            query=query, answer=Answer(text="ov", citations=[], query_id="q1"),
            results=[SearchHit(doc_id="d1", title="T", source="sharepoint", source_url="http://x",
                               author_id="u1", modified_at=datetime(2026, 5, 31, tzinfo=UTC), snippet="s")],
            facets=[SourceFacet(source="sharepoint", count=1)], people=[], total=1)


def test_search_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.post("/search", json={"query": "x"}).status_code == 401


def test_search_returns_response() -> None:
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    try:
        with TestClient(app) as client:
            resp = client.post("/search", json={"query": "vision", "sources": ["sharepoint"]}, headers=_HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]["text"] == "ov"
        assert body["results"][0]["doc_id"] == "d1"
        assert body["facets"][0]["count"] == 1
        assert body["total"] == 1
    finally:
        app.dependency_overrides.clear()


def test_search_empty_when_service_unavailable() -> None:
    app.dependency_overrides[get_search_service] = lambda: None
    try:
        with TestClient(app) as client:
            resp = client.post("/search", json={"query": "vision"}, headers=_HDR)
        assert resp.status_code == 200
        assert resp.json() == {"query": "vision", "answer": None, "results": [],
                               "facets": [], "people": [], "authors": [], "total": 0}
    finally:
        app.dependency_overrides.clear()
