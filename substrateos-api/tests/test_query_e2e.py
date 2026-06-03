import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_query_returns_grounded_answer_with_citations() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "what is our PTO policy?"},
            headers={"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" not in body  # field is named `text`
    assert isinstance(body["text"], str) and len(body["text"]) > 10
    assert len(body["citations"]) >= 1
    assert body["query_id"]
