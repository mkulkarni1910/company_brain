import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_query_includes_debug_signals_when_requested() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?", "include_debug": True},
            headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["debug"] is not None
        sig = body["debug"]["signals"]
        for k in ("content", "people", "activity", "recency"):
            assert k in sig
        assert body["debug"]["candidates_ranked"] >= 1
        assert "live_used" in body["debug"]


@pytest.mark.integration
def test_query_omits_debug_by_default() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?"},
            headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"},
        )
        assert resp.status_code == 200
        assert resp.json()["debug"] is None
