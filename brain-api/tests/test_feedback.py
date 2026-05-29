import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_feedback_ingests_event() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/feedback",
            json={"doc_id": "up:policy-pto", "signal": "click", "dwell_ms": 4200},
            headers={"x-debug-bypass-auth": "t-test,u-fb,t-test:everyone"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "recorded"
