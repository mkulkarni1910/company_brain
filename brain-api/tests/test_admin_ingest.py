from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_post_admin_ingest_returns_count() -> None:
    now = datetime.now(UTC).isoformat()
    payload = {
        "doc_id": "up:admin-ingest-test",
        "tenant_id": "t-test",
        "source": "uploaded",
        "source_url": "local://admin-ingest-test",
        "title": "Admin Ingest Test",
        "body": "# Test\n\nHello world. This is a test document.",
        "author_id": None,
        "acl_principals": ["t-test:everyone"],
        "created_at": now,
        "modified_at": now,
        "mime": "text/markdown",
    }
    client = TestClient(app)
    resp = client.post("/admin/ingest", json=payload)
    assert resp.status_code == 200
    assert resp.json()["chunks_indexed"] >= 1
