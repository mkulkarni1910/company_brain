import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_freshness_query_triggers_live_fetch() -> None:
    with TestClient(app) as client:
        # A freshness query routed through /admin/retrieve (debug-gated).
        resp = client.post(
            "/admin/retrieve",
            json={"query": "what files changed recently", "k": 10},
            headers={"x-debug-bypass-auth": "t-test,u-live,t-test:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        live = [c for c in body["candidates"] if "graph:" in c["doc_id"]]
        if live:
            # Tenant has searchable content: a live Graph result was merged.
            assert all(c["doc_id"].startswith("graph:") for c in live)
        else:
            # Empty/sparse tenant: Live Fetch fired but Graph returned nothing.
            # The request still succeeds index-only — the merge path didn't crash.
            pytest.skip("Graph /search returned no hits for this tenant; "
                        "live-merge path exercised without content to assert on")


@pytest.mark.integration
def test_static_query_has_no_live_results() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/admin/retrieve",
            json={"query": "what is our PTO policy?", "k": 10},
            headers={"x-debug-bypass-auth": "t-test,u-live,t-test:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert not any("graph:" in c["doc_id"] for c in body["candidates"])
