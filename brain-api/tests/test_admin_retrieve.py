import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_admin_retrieve_returns_ranked_doc_ids() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/admin/retrieve",
            json={"query": "PTO policy", "k": 10},
            headers={"x-debug-bypass-auth": "t-eval,u-eval,t-eval:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "doc_ids" in body
        assert isinstance(body["doc_ids"], list)
        # the PTO doc should be retrieved for a PTO query
        assert any("pto" in d.lower() for d in body["doc_ids"])
