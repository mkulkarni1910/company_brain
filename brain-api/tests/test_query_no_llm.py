import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_query_returns_candidates_no_llm() -> None:
    client = TestClient(app)
    resp = client.post(
        "/query",
        json={"query": "PTO policy", "k": 5},
        headers={"x-debug-bypass-auth": "t-test,u-test,t-test:everyone"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "candidates" in body
    assert len(body["candidates"]) > 0
    assert any("pto" in (c["chunk"]["doc_id"] or "").lower() for c in body["candidates"])
